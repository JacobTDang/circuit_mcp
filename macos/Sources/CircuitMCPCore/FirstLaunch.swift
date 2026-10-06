import Foundation

/// The one first-run question that belongs to the filesystem rather than AppKit. A missing data
/// folder and an existing empty one mean the same thing: the app has no notebook to show yet.
public enum FirstLaunch {
    public static func shouldOfferSetup(dataDirectory: URL,
                                        fileManager: FileManager = .default) throws -> Bool {
        guard fileManager.fileExists(atPath: dataDirectory.path) else { return true }
        return try fileManager.contentsOfDirectory(atPath: dataDirectory.path).isEmpty
    }
}
