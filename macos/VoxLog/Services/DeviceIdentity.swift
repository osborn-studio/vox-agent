import Foundation
import IOKit

/// Persistent device identity for cross-machine sync.
enum DeviceIdentity {

    private static let key = "voxlog_device_id"

    /// Stable device ID (hardware UUID, persists across reinstalls).
    static var deviceId: String {
        if let existing = UserDefaults.standard.string(forKey: key) {
            return existing
        }
        let id = hardwareUUID() ?? UUID().uuidString.lowercased()
        UserDefaults.standard.set(id, forKey: key)
        return id
    }

    /// Human-readable device name (e.g. "OsborN's Mac mini").
    static var deviceName: String {
        Host.current().localizedName ?? ProcessInfo.processInfo.hostName
    }

    /// Short name for directory naming (e.g. "osborns-mac-mini").
    static var shortName: String {
        ProcessInfo.processInfo.hostName
            .replacingOccurrences(of: ".local", with: "")
            .replacingOccurrences(of: " ", with: "-")
            .replacingOccurrences(of: "'", with: "")
            .lowercased()
    }

    private static func hardwareUUID() -> String? {
        let service = IOServiceGetMatchingService(kIOMainPortDefault,
                                                  IOServiceMatching("IOPlatformExpertDevice"))
        guard service != 0 else { return nil }
        defer { IOObjectRelease(service) }
        return (IORegistryEntryCreateCFProperty(service, kIOPlatformUUIDKey as CFString,
                                                kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? String)?.lowercased()
    }
}
