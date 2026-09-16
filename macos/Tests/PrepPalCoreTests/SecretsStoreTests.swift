import XCTest
@testable import PrepPalCore

final class SecretsStoreTests: XCTestCase {
    private var store: SecretsStore!

    override func setUpWithError() throws {
        store = SecretsStore(service: "io.github.jacobtdang.preppal.tests.\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try store.delete(SecretsStore.apiKeyAccount)
        try store.delete(SecretsStore.modelAccount)
    }

    func testAMissingSecretReadsAsNil() throws {
        XCTAssertNil(try store.read(SecretsStore.apiKeyAccount))
    }

    func testWriteThenReadAndOverwrite() throws {
        try store.write("sk-or-first", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.read(SecretsStore.apiKeyAccount), "sk-or-first")
        try store.write("sk-or-second", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.read(SecretsStore.apiKeyAccount), "sk-or-second")
    }

    func testDeleteRemovesTheSecretAndToleratesAMissingOne() throws {
        try store.write("qwen/qwen3-coder:free", for: SecretsStore.modelAccount)
        try store.delete(SecretsStore.modelAccount)
        XCTAssertNil(try store.read(SecretsStore.modelAccount))
        try store.delete(SecretsStore.modelAccount)
    }

    func testServerSecretsIncludeOnlyWhatIsSet() throws {
        XCTAssertEqual(try store.serverSecrets(), [:])
        try store.write("sk-or-key", for: SecretsStore.apiKeyAccount)
        XCTAssertEqual(try store.serverSecrets(), ["OPENROUTER_API_KEY": "sk-or-key"])
    }

    /// Two stores must not see each other's items: the service name is the only thing
    /// separating the app's secrets from another application's identically named accounts.
    func testAnotherServiceDoesNotSeeTheseSecrets() throws {
        try store.write("sk-or-key", for: SecretsStore.apiKeyAccount)
        let other = SecretsStore(service: "io.github.jacobtdang.preppal.tests.\(UUID().uuidString)")
        XCTAssertNil(try other.read(SecretsStore.apiKeyAccount))
        XCTAssertEqual(try other.serverSecrets(), [:])
    }

    /// Every other test here injects its own service name, so nothing else would catch a typo in
    /// the default -- and a typo writes the key to a service the running app never reads.
    func testTheDefaultServiceIsTheAppsBundleIdentifier() {
        XCTAssertEqual(SecretsStore().service, "io.github.jacobtdang.preppal")
    }
}
