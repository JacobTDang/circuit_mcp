import XCTest
@testable import CircuitMCPCore

final class SecretsStoreTests: XCTestCase {
    private var store: SecretsStore!

    override func setUpWithError() throws {
        store = SecretsStore(service: "io.github.jacobtdang.circuitmcp.tests.\(UUID().uuidString)")
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

    /// Settings shows this text when a read fails, so it must be the Keychain's own sentence,
    /// not the enum case and a bare status number.
    func testAKeychainErrorReadsAsTheKeychainsOwnSentence() {
        let error = SecretsStoreError.unexpectedStatus(errSecAuthFailed, operation: "read OPENROUTER_API_KEY")
        let macOS = SecCopyErrorMessageString(errSecAuthFailed, nil) as String?

        XCTAssertNotNil(macOS)
        XCTAssertEqual("\(error)", "The Keychain could not read OPENROUTER_API_KEY (OSStatus -25293): \(macOS!)")
        XCTAssertEqual(error.localizedDescription, "\(error)")
        XCTAssertEqual("\(SecretsStoreError.notUTF8(account: "OPENROUTER_MODEL"))",
                       "The Keychain item OPENROUTER_MODEL is not UTF-8 text.")
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
        let other = SecretsStore(service: "io.github.jacobtdang.circuitmcp.tests.\(UUID().uuidString)")
        XCTAssertNil(try other.read(SecretsStore.apiKeyAccount))
        XCTAssertEqual(try other.serverSecrets(), [:])
    }

    /// Every other test here injects its own service name, so nothing else would catch a typo in
    /// the default -- and a typo writes the key to a service the running app never reads.
    func testTheDefaultServiceIsTheAppsBundleIdentifier() {
        XCTAssertEqual(SecretsStore().service, "io.github.jacobtdang.circuitmcp")
    }
}
