import copy
import json
import unittest

from telegram_bridge.control import GateError
from telegram_bridge.t3 import T3Delegation,parse_coordinator,selected_context
from test_handoff import snapshot


def reply(intent,value,quote):
    return json.dumps({'reply':'Verstanden','answer':{'intent':intent,'confirmed':True,
        'confirmation_quote':quote,'answers':{'choice':value}}})


class DialogClient:
    def __init__(self,new_ask=False):
        self.data=snapshot();self.new_ask=new_ask
        self.data['thread']['latestTurn']={'turnId':'turn','state':'running'}
        self.data['thread']['runtimeMode']='approval-required'
        self.data['thread']['interactionMode']='plan'
        self.ask_answers=[];self.followups=[];self.responses=[];self.prompts=[]
    def snapshot(self,_):return copy.deepcopy(self.data)
    def create_coordinator(self,_):return 'coordinator'
    def run_coordinator(self,_,prompt,**kwargs):
        self.prompts.append(prompt)
        return self.responses.pop(0)
    def return_answer(self,handoff,answers,**kwargs):
        self.ask_answers.append((handoff.request_id,answers))
        thread=self.data['thread'];index=len(self.ask_answers)
        thread['activities'].append({'id':'resolved-'+str(index),'createdAt':f'2026-09-11T10:0{index*2}:00Z',
            'kind':'user-input.resolved','payload':{'requestId':handoff.request_id}})
        thread['messages'].append({'id':'source-reply-'+str(index),'role':'assistant','streaming':False,
            'text':'Es geht um den Bericht für den Vorstand.' if index==1 else 'PDF ist bestätigt.'})
        thread['latestTurn']['state']='completed'
        if self.new_ask and index==1:
            thread['activities'].append({'id':'fresh-question','createdAt':'2026-09-11T10:03:00Z',
                'kind':'user-input.requested','payload':{'requestId':'request-2',
                    'questions':[{'id':'choice','question':'Soll der Vorstandsbericht als CSV oder PDF kommen?'}]}})
            thread['latestTurn']['state']='running'
        return True
    def start_source_followup(self,thread_id,text,command_id):
        self.followups.append((thread_id,text,command_id))
        self.data['thread']['messages'].append({'id':'followup-result','role':'assistant','streaming':False,'text':'PDF ist bestätigt.'})
    def wait_for_source_reply(self,*args,**kwargs):return copy.deepcopy(self.data)


class T3DialogTests(unittest.TestCase):
    def test_clarification_fetches_source_reply_and_does_not_finish_decision(self):
        client=DialogClient();client.responses=[reply('clarification','Welcher Bericht ist gemeint?','Welcher Bericht?')]
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture')
        response=bridge([{'role':'user','text':'Welcher Bericht?'}])
        self.assertIn('Vorstand',response)
        self.assertIn('noch nicht getroffen',response)
        self.assertFalse(bridge.completed)
        self.assertTrue(bridge.submitted)
        self.assertEqual(client.ask_answers[0][1],{'choice':'Welcher Bericht ist gemeint?'})

    def test_decision_after_clarification_uses_followup_not_closed_request(self):
        client=DialogClient();client.responses=[reply('clarification','Welcher Bericht?','Welcher Bericht?'),reply('decision','PDF','Ja, PDF.')]
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture')
        bridge([{'role':'user','text':'Welcher Bericht?'}])
        response=bridge([{'role':'assistant','text':'PDF für den Vorstand?'},{'role':'user','text':'Ja, PDF.'}])
        self.assertTrue(bridge.completed)
        self.assertEqual(len(client.ask_answers),1)
        self.assertEqual(len(client.followups),1)
        self.assertIn('PDF',client.followups[0][1])
        self.assertIn('PDF',response)
        bridge([{'role':'user','text':'Ja, PDF.'}])
        self.assertEqual(len(client.followups),1)

    def test_new_ask_is_adopted_and_requires_new_user_input(self):
        client=DialogClient(new_ask=True)
        client.responses=[reply('clarification','Welcher Bericht?','Welcher Bericht?'),reply('decision','PDF','Ja, PDF.')]
        revision=[1]
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture');bridge.revision=lambda:revision[0]
        response=bridge([{'role':'user','text':'Welcher Bericht?'}],revision=1)
        self.assertIn('Neue Rückfrage',response)
        self.assertEqual(bridge.handoff.request_id,'request-2')
        bridge([{'role':'user','text':'Welcher Bericht?'}],revision=1)
        self.assertEqual(len(client.ask_answers),1)
        revision[0]=2
        bridge([{'role':'assistant','text':'PDF?'},{'role':'user','text':'Ja, PDF.'}],revision=2)
        self.assertEqual([r for r,_ in client.ask_answers],['request-1','request-2'])
        self.assertFalse(client.followups)
        self.assertTrue(bridge.completed)

    def test_structurer_answers_without_creating_a_coordinator_thread(self):
        client=DialogClient();client.create_coordinator=lambda _:self.fail('coordinator thread created')
        prompts=[]
        def structurer(prompt):
            prompts.append(prompt);return reply('decision','PDF','Ja, PDF.')
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture',structurer=structurer)
        bridge([{'role':'assistant','text':'PDF für den Vorstand?'},{'role':'user','text':'Ja, PDF.'}])
        self.assertEqual(client.ask_answers,[('request-1',{'choice':'PDF'})])
        self.assertTrue(bridge.completed)
        self.assertIsNone(bridge.coordinator_id)
        self.assertEqual(client.prompts,[])
        self.assertIn('Ja, PDF.',prompts[0])

    def test_structurer_still_needs_the_quote_in_the_latest_user_statement(self):
        client=DialogClient()
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture',
            structurer=lambda _:reply('decision','PDF','Ja, PDF.'))
        bridge([{'role':'user','text':'Nein, lieber CSV.'}])
        self.assertEqual(client.ask_answers,[])
        self.assertFalse(bridge.submitted)

    def test_failing_structurer_falls_back_to_the_coordinator_thread(self):
        client=DialogClient();client.responses=[reply('decision','PDF','Ja, PDF.')]
        events=[]
        def structurer(_):raise GateError('structurer_http_500')
        bridge=T3Delegation(client,'t3-thread','request-1',context='Fixture',emit=events.append,structurer=structurer)
        bridge([{'role':'user','text':'Ja, PDF.'}])
        self.assertEqual(client.ask_answers,[('request-1',{'choice':'PDF'})])
        self.assertEqual(bridge.coordinator_id,'coordinator')
        self.assertIn({'structurer_fallback':'structurer_http_500'},events)

    def test_structurer_is_not_part_of_the_durable_state(self):
        bridge=T3Delegation(DialogClient(),'t3-thread','request-1',context='Fixture',structurer=lambda _:'{}')
        self.assertNotIn('structurer',json.dumps(bridge.state()))
        self.assertIsNone(T3Delegation.restore(DialogClient(),bridge.state()).structurer)

    def test_missing_intent_cannot_turn_a_query_into_a_decision(self):
        ambiguous={'reply':'Verstanden','answer':{'confirmed':True,'confirmation_quote':'Ja','answers':{'choice':'Bitte erst klären'}}}
        with self.assertRaisesRegex(GateError,'intent'):
            parse_coordinator(json.dumps(ambiguous),[{'role':'user','text':'Ja'}])

    def test_context_contains_relevant_task_text_but_redacts_keys(self):
        data={'title':'Wochenbericht','messages':[{'role':'user','text':'Fiktiver Vorstandsbericht. sk-proj-'+'x'*40},
            {'role':'tool','text':'SECRET_TOOL_OUTPUT'},{'role':'assistant','text':'CSV oder PDF?'}]}
        context=selected_context(data)
        self.assertIn('Vorstandsbericht',context)
        self.assertNotIn('sk-proj-',context)
        self.assertNotIn('SECRET_TOOL_OUTPUT',context)


if __name__=='__main__':unittest.main()
