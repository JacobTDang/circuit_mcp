import Foundation

public enum AppLocationsError: Error, Equatable {
    case emptyHome
}

/// Where the app keeps its data and logs, and where its bundled Python lives.
public struct AppLocations: Equatable {
    public static let folderName = "PrepPal"

    public let supportDirectory: URL
    public let logsDirectory: URL

    public var commandCenterDirectory: URL { supportDirectory.appendingPathComponent("command_center", isDirectory: true) }
    public var showmanDirectory: URL { supportDirectory.appendingPathComponent("showman", isDirectory: true) }
    public var workspaceConfig: URL { supportDirectory.appendingPathComponent("workspace.json") }

    /// `PREPPAL_HOME`, when set, replaces `~/Library` so tests never touch real user data.
    public static func current(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        homeLibrary: URL = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
    ) throws -> AppLocations {
        var library = homeLibrary
        if let home = environment["PREPPAL_HOME"] {
            guard !home.trimmingCharacters(in: .whitespaces).isEmpty else { throw AppLocationsError.emptyHome }
            library = URL(fileURLWithPath: home, isDirectory: true)
        }
        return AppLocations(
            supportDirectory: library.appendingPathComponent("Application Support", isDirectory: true)
                .appendingPathComponent(folderName, isDirectory: true),
            logsDirectory: library.appendingPathComponent("Logs", isDirectory: true)
                .appendingPathComponent(folderName, isDirectory: true)
        )
    }

    public func createDirectories(fileManager: FileManager = .default) throws {
        try fileManager.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
    }

    public static func bundledPython(in bundle: URL) -> URL {
        bundle.appendingPathComponent("Contents/Resources/python/bin/python3")
    }

    /// The simulator the app carries, staged by `macos/stage_ngspice.sh`.
    public static func bundledNgspice(in bundle: URL) -> URL {
        bundle.appendingPathComponent("Contents/Resources/ngspice/bin/ngspice")
    }

    /// The AirPlay receiver the app carries, staged by `macos/stage_uxplay.sh`.
    public static func bundledUxplay(in bundle: URL) -> URL {
        bundledGStreamer(in: bundle).appendingPathComponent("bin/uxplay")
    }

    /// The staged receiver's own folder: binary, plugins and plugin scanner.
    public static func bundledGStreamer(in bundle: URL) -> URL {
        bundle.appendingPathComponent("Contents/Resources/uxplay", isDirectory: true)
    }

    /// macOS runs a quarantined app from a randomized read-only copy until the user moves it.
    public static func isTranslocated(_ bundle: URL) -> Bool {
        bundle.path.contains("/AppTranslocation/")
    }
}
