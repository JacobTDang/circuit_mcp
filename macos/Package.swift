// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "PrepPal",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "PrepPalCore"),
        .executableTarget(name: "PrepPal", dependencies: ["PrepPalCore"]),
        .testTarget(name: "PrepPalCoreTests", dependencies: ["PrepPalCore"], resources: [.copy("Fixtures")]),
    ]
)
