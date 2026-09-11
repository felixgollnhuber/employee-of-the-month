import Foundation
import Security

// stdin/stdout are private pipes owned by the Python bridge. Never use this
// helper's `get` command as a diagnostic command or log its stdout.
let arguments = CommandLine.arguments
guard arguments.count == 3,
      ["get", "set"].contains(arguments[1]),
      arguments[2].range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { exit(2) }
let operation = arguments[1]
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "CodexPhoneBridge.Telegram.DatabaseKey.v1",
    kSecAttrAccount as String: arguments[2],
]
func fail(_ status: OSStatus) -> Never {
    FileHandle.standardError.write(Data("keychain_status=\(status)\n".utf8))
    exit(status == errSecItemNotFound ? 3 : 4)
}
if operation == "get" {
    SecKeychainSetUserInteractionAllowed(false)
    var search = query
    search[kSecMatchLimit as String] = kSecMatchLimitOne
    search[kSecReturnData as String] = true
    var result: CFTypeRef?
    let status = SecItemCopyMatching(search as CFDictionary, &result)
    guard status == errSecSuccess else { fail(status) }
    guard let data = result as? Data, data.count == 32 else { exit(5) }
    FileHandle.standardOutput.write(Data((data.base64EncodedString() + "\n").utf8))
} else {
    guard let line = readLine(), line.count == 44,
          let data = Data(base64Encoded: line), data.count == 32 else { exit(2) }
    let update = [kSecValueData as String: data]
    var status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
    if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrLabel as String] = "Mitarbeiter des Monats - Telegram-Datenbank"
        status = SecItemAdd(item as CFDictionary, nil)
    }
    guard status == errSecSuccess else { fail(status) }
    print("stored")
}
