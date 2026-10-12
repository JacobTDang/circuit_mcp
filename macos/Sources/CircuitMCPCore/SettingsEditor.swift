import Foundation

public protocol SettingsStoring {
    func read(_ account: String) throws -> String?
    func write(_ value: String, for account: String) throws
    func delete(_ account: String) throws
}

public enum SettingLoadState: Equatable {
    case available(String?)
    case unavailable(String)
}

public struct SettingsLoad: Equatable {
    public let apiKey: SettingLoadState
    public let model: SettingLoadState

    public var canSave: Bool {
        guard case .available = apiKey, case .available = model else { return false }
        return true
    }
}

public enum SettingsUpdateError: LocalizedError, Equatable {
    case valuesUnavailable
    case updateFailed(account: String, reason: String)
    case rollbackFailed(account: String, reason: String, rollbackAccount: String,
                        rollbackReason: String)

    public var errorDescription: String? {
        switch self {
        case .valuesUnavailable:
            return "Current settings are unavailable. Reopen Settings after fixing Keychain access."
        case .updateFailed(let account, let reason):
            return "The settings were not changed. Updating \(account) failed: \(reason)."
        case .rollbackFailed(let account, let reason, let rollbackAccount, let rollbackReason):
            return "Updating \(account) failed: \(reason). Restoring \(rollbackAccount) also failed: \(rollbackReason). \(rollbackAccount) may have changed; reopen Settings before trying again."
        }
    }
}

public struct SettingsEditor {
    private let store: any SettingsStoring

    public init(store: any SettingsStoring) {
        self.store = store
    }

    public func load() -> SettingsLoad {
        SettingsLoad(apiKey: load(SecretsStore.apiKeyAccount),
                     model: load(SecretsStore.modelAccount))
    }

    public func save(apiKey: String, model: String, original: SettingsLoad) throws {
        guard original.canSave,
              case .available(let oldAPIKey) = original.apiKey,
              case .available(let oldModel) = original.model else {
            throw SettingsUpdateError.valuesUnavailable
        }
        let requested = [
            Change(account: SecretsStore.apiKeyAccount, oldValue: oldAPIKey,
                   newValue: normalized(apiKey)),
            Change(account: SecretsStore.modelAccount, oldValue: oldModel,
                   newValue: normalized(model)),
        ].filter { $0.oldValue != $0.newValue }

        var applied: [Change] = []
        for change in requested {
            do {
                try apply(change.newValue, to: change.account)
                applied.append(change)
            } catch {
                let updateReason = String(describing: error)
                for prior in applied.reversed() {
                    do {
                        try apply(prior.oldValue, to: prior.account)
                    } catch {
                        throw SettingsUpdateError.rollbackFailed(
                            account: change.account, reason: updateReason,
                            rollbackAccount: prior.account, rollbackReason: String(describing: error))
                    }
                }
                throw SettingsUpdateError.updateFailed(account: change.account, reason: updateReason)
            }
        }
    }

    private func load(_ account: String) -> SettingLoadState {
        do {
            return .available(try store.read(account))
        } catch {
            return .unavailable(String(describing: error))
        }
    }

    private func normalized(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func apply(_ value: String?, to account: String) throws {
        if let value {
            try store.write(value, for: account)
        } else {
            try store.delete(account)
        }
    }

    private struct Change {
        let account: String
        let oldValue: String?
        let newValue: String?
    }
}
