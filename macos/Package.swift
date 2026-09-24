// swift-tools-version:5.10
import PackageDescription

// Under the Swift 5 language mode a main-actor violation is only a warning in some positions, and
// the whole point of annotating this target is that the compiler enforces the rule rather than a
// doc comment. Promoting that one diagnostic group makes it enforcement.
let mainActorIsEnforced: [SwiftSetting] = [.unsafeFlags(["-Werror", "ActorIsolatedCall"])]

let package = Package(
    name: "CircuitMCP",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "CircuitMCPCore", swiftSettings: mainActorIsEnforced),
        .executableTarget(name: "CircuitMCP", dependencies: ["CircuitMCPCore"], swiftSettings: mainActorIsEnforced),
        .testTarget(name: "CircuitMCPCoreTests", dependencies: ["CircuitMCPCore"],
                    resources: [.copy("Fixtures")], swiftSettings: mainActorIsEnforced),
    ]
)
