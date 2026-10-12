import XCTest
@testable import CircuitMCPCore

final class SettingsEditorTests: XCTestCase {
    private enum TestError: Error, Equatable { case failed(String) }

    private final class MemoryStore: SettingsStoring {
        var values: [String: String]
        var failures: [String: Int] = [:]
        private(set) var mutations: [String] = []

        init(_ values: [String: String]) { self.values = values }

        func read(_ account: String) throws -> String? {
            if shouldFail("read:\(account)") { throw TestError.failed("read \(account)") }
            return values[account]
        }

        func write(_ value: String, for account: String) throws {
            mutations.append("write:\(account):\(value)")
            if shouldFail("write:\(account)") { throw TestError.failed("write \(account)") }
            values[account] = value
        }

        func delete(_ account: String) throws {
            mutations.append("delete:\(account)")
            if shouldFail("delete:\(account)") { throw TestError.failed("delete \(account)") }
            values.removeValue(forKey: account)
        }

        private func shouldFail(_ operation: String) -> Bool {
            guard let remaining = failures[operation], remaining > 0 else { return false }
            failures[operation] = remaining - 1
            return true
        }
    }

    private let original = [
        SecretsStore.apiKeyAccount: "sk-existing",
        SecretsStore.modelAccount: "old/model:free",
    ]

    func testAPIKeyReadFailureKeepsTheModelVisibleAndMakesSaveUnavailable() {
        let store = MemoryStore(original)
        store.failures["read:\(SecretsStore.apiKeyAccount)"] = 1

        let loaded = SettingsEditor(store: store).load()

        XCTAssertEqual(loaded.apiKey, .unavailable("failed(\"read OPENROUTER_API_KEY\")"))
        XCTAssertEqual(loaded.model, .available("old/model:free"))
        XCTAssertFalse(loaded.canSave)
        XCTAssertEqual(store.values, original)
    }

    func testModelReadFailureKeepsTheAPIKeyVisibleAndMakesSaveUnavailable() {
        let store = MemoryStore(original)
        store.failures["read:\(SecretsStore.modelAccount)"] = 1

        let loaded = SettingsEditor(store: store).load()

        XCTAssertEqual(loaded.apiKey, .available("sk-existing"))
        XCTAssertEqual(loaded.model, .unavailable("failed(\"read OPENROUTER_MODEL\")"))
        XCTAssertFalse(loaded.canSave)
        XCTAssertEqual(store.values, original)
    }

    func testFirstWriteFailureLeavesBothExistingValuesIntact() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()
        store.failures["write:\(SecretsStore.apiKeyAccount)"] = 1

        XCTAssertThrowsError(try editor.save(apiKey: "sk-new", model: "new/model:free", original: loaded))
        XCTAssertEqual(store.values, original)
    }

    func testSecondWriteFailureRollsBackTheFirstWrite() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()
        store.failures["write:\(SecretsStore.modelAccount)"] = 1

        XCTAssertThrowsError(try editor.save(apiKey: "sk-new", model: "new/model:free", original: loaded)) {
            XCTAssertEqual(($0 as? SettingsUpdateError)?.errorDescription,
                           "The settings were not changed. Updating OPENROUTER_MODEL failed: failed(\"write OPENROUTER_MODEL\").")
        }
        XCTAssertEqual(store.values, original)
        XCTAssertEqual(store.mutations.last, "write:\(SecretsStore.apiKeyAccount):sk-existing")
    }

    func testFirstDeleteFailureLeavesBothExistingValuesIntact() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()
        store.failures["delete:\(SecretsStore.apiKeyAccount)"] = 1

        XCTAssertThrowsError(try editor.save(apiKey: "   ", model: "new/model:free", original: loaded))
        XCTAssertEqual(store.values, original)
    }

    func testSecondDeleteFailureRollsBackTheFirstWrite() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()
        store.failures["delete:\(SecretsStore.modelAccount)"] = 1

        XCTAssertThrowsError(try editor.save(apiKey: "sk-new", model: "", original: loaded))
        XCTAssertEqual(store.values, original)
        XCTAssertEqual(store.mutations.last, "write:\(SecretsStore.apiKeyAccount):sk-existing")
    }

    func testRollbackFailureReportsThatTheFirstSettingMayHaveChanged() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()
        store.failures["write:\(SecretsStore.modelAccount)"] = 1
        store.failures["write:\(SecretsStore.apiKeyAccount)"] = 1

        XCTAssertThrowsError(try editor.save(apiKey: "", model: "new/model:free", original: loaded)) {
            XCTAssertEqual(($0 as? SettingsUpdateError)?.errorDescription,
                           "Updating OPENROUTER_MODEL failed: failed(\"write OPENROUTER_MODEL\"). Restoring OPENROUTER_API_KEY also failed: failed(\"write OPENROUTER_API_KEY\"). OPENROUTER_API_KEY may have changed; reopen Settings before trying again.")
        }
        XCTAssertNil(store.values[SecretsStore.apiKeyAccount])
        XCTAssertEqual(store.values[SecretsStore.modelAccount], original[SecretsStore.modelAccount])
    }

    func testWhitespaceIsTrimmedAndUnchangedValuesAreNotMutated() throws {
        let store = MemoryStore(original)
        let editor = SettingsEditor(store: store)
        let loaded = editor.load()

        try editor.save(apiKey: "  sk-existing  ", model: " new/model:free\n", original: loaded)

        XCTAssertEqual(store.values[SecretsStore.modelAccount], "new/model:free")
        XCTAssertEqual(store.mutations, ["write:\(SecretsStore.modelAccount):new/model:free"])
    }
}
