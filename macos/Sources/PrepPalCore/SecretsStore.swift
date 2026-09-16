import Foundation
import Security

public enum SecretsStoreError: Error, Equatable {
    case unexpectedStatus(OSStatus, operation: String)
    case notUTF8(account: String)
}

/// The OpenRouter key and model, kept as generic passwords in the login Keychain.
public struct SecretsStore {
    public static let apiKeyAccount = "OPENROUTER_API_KEY"
    public static let modelAccount = "OPENROUTER_MODEL"

    public let service: String

    public init(service: String = "io.github.jacobtdang.preppal") {
        self.service = service
    }

    private func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func read(_ account: String) throws -> String? {
        var request = query(account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw SecretsStoreError.unexpectedStatus(status, operation: "read \(account)") }
        guard let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            throw SecretsStoreError.notUTF8(account: account)
        }
        return value
    }

    public func write(_ value: String, for account: String) throws {
        let data = Data(value.utf8)
        let updated = SecItemUpdate(query(account) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else {
            throw SecretsStoreError.unexpectedStatus(updated, operation: "update \(account)")
        }
        var item = query(account)
        item[kSecValueData as String] = data
        let added = SecItemAdd(item as CFDictionary, nil)
        guard added == errSecSuccess else { throw SecretsStoreError.unexpectedStatus(added, operation: "add \(account)") }
    }

    public func delete(_ account: String) throws {
        let status = SecItemDelete(query(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SecretsStoreError.unexpectedStatus(status, operation: "delete \(account)")
        }
    }

    public func serverSecrets() throws -> [String: String] {
        var secrets: [String: String] = [:]
        for account in [Self.apiKeyAccount, Self.modelAccount] {
            if let value = try read(account) { secrets[account] = value }
        }
        return secrets
    }
}
