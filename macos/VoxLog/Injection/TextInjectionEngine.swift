import AppKit
import ApplicationServices
import Carbon.HIToolbox

final class TextInjectionEngine: @unchecked Sendable {

    private struct FocusedElementSnapshot {
        let bundleIdentifier: String?
        let role: String?
        let value: String?
        let isEditable: Bool
        /// true when AX successfully found a focused UI element; false when
        /// no element was found (e.g. desktop, no focused window).
        let hasFocusedElement: Bool
    }

    private struct ClipboardSnapshot {
        /// Only safe, non-blocking text types are captured.
        /// Binary types (images, RTF, file promises) are skipped because
        /// reading them can trigger lazy data providers in other apps,
        /// blocking the calling thread indefinitely.
        private static let safeTypes: [NSPasteboard.PasteboardType] = [
            .string,
            .URL,
            .html,
            NSPasteboard.PasteboardType("public.utf8-plain-text"),
            NSPasteboard.PasteboardType("public.utf16-plain-text"),
            NSPasteboard.PasteboardType("public.url"),
        ]

        struct Item {
            let types: [NSPasteboard.PasteboardType]
            let data: [NSPasteboard.PasteboardType: Data]
        }
        let items: [Item]
        let changeCount: Int

        static func capture() -> ClipboardSnapshot {
            let pb = NSPasteboard.general
            let changeCount = pb.changeCount
            let safeSet = Set(safeTypes.map(\.rawValue))
            var items: [Item] = []
            for pbItem in pb.pasteboardItems ?? [] {
                let textTypes = pbItem.types.filter { safeSet.contains($0.rawValue) }
                guard !textTypes.isEmpty else { continue }
                var dataMap: [NSPasteboard.PasteboardType: Data] = [:]
                for type in textTypes {
                    if let data = pbItem.data(forType: type) {
                        dataMap[type] = data
                    }
                }
                items.append(Item(types: textTypes, data: dataMap))
            }
            return ClipboardSnapshot(items: items, changeCount: changeCount)
        }

        func restore(expectedChangeCount: Int) {
            let pb = NSPasteboard.general
            guard !items.isEmpty else { return }
            guard pb.changeCount == expectedChangeCount else { return }
            pb.clearContents()
            for item in items {
                let pbItem = NSPasteboardItem()
                for type in item.types {
                    if let data = item.data[type] {
                        pbItem.setData(data, forType: type)
                    }
                }
                pb.writeObjects([pbItem])
            }
        }
    }

    // MARK: - Public

    /// When true, saves and restores the clipboard around injection.
    /// Has a small race-condition risk: if the target app hasn't finished
    /// reading the clipboard before restore, the paste may contain stale data.
    var preserveClipboard = true

    /// Inject text into the currently focused input field.
    /// Returns the outcome as soon as the paste is dispatched.
    /// Call ``finishClipboardRestore()`` afterward to restore the original clipboard.
    func inject(_ text: String) -> InjectionOutcome {
        guard !text.isEmpty else { return .inserted }
        return injectViaClipboard(text)
    }

    /// Restore the clipboard that was saved before injection.
    /// Safe to call even if there's nothing to restore.
    func finishClipboardRestore() {
        guard let pending = pendingClipboardRestore else { return }
        pendingClipboardRestore = nil
        usleep(50_000)
        pending.snapshot.restore(expectedChangeCount: pending.changeCount)
    }

    /// Copy text to the system clipboard (used at session end).
    func copyToClipboard(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    // MARK: - Clipboard injection

    private struct PendingClipboardRestore {
        let snapshot: ClipboardSnapshot
        let changeCount: Int
    }

    private var pendingClipboardRestore: PendingClipboardRestore?

    private func injectViaClipboard(_ text: String) -> InjectionOutcome {
        let savedClipboard = preserveClipboard ? ClipboardSnapshot.capture() : nil

        // Check if there's a frontmost app to paste into (lightweight, no AX)
        let hasFrontmostApp = NSWorkspace.shared.frontmostApplication != nil

        copyToClipboard(text)
        let postWriteChangeCount = NSPasteboard.general.changeCount
        usleep(50_000)
        simulatePaste()
        usleep(100_000)

        let outcome: InjectionOutcome = hasFrontmostApp ? .inserted : .copiedToClipboard

        // Defer clipboard restore so .finalized can be emitted sooner
        if outcome == .inserted, let savedClipboard {
            pendingClipboardRestore = PendingClipboardRestore(
                snapshot: savedClipboard, changeCount: postWriteChangeCount
            )
        } else {
            pendingClipboardRestore = nil
        }

        return outcome
    }

    private func simulatePaste() {
        let vKeyCode: CGKeyCode = 9 // 'v'

        guard let keyDown = CGEvent(keyboardEventSource: nil, virtualKey: vKeyCode, keyDown: true),
              let keyUp = CGEvent(keyboardEventSource: nil, virtualKey: vKeyCode, keyDown: false)
        else { return }

        keyDown.flags = .maskCommand
        keyUp.flags = .maskCommand

        keyDown.post(tap: .cghidEventTap)
        keyUp.post(tap: .cghidEventTap)
    }

    private func captureFocusedElementSnapshot() -> FocusedElementSnapshot? {
        let frontmostBundleID = NSWorkspace.shared.frontmostApplication?.bundleIdentifier

        guard AXIsProcessTrusted() else {
            return FocusedElementSnapshot(
                bundleIdentifier: frontmostBundleID,
                role: nil,
                value: nil,
                isEditable: false,
                hasFocusedElement: false
            )
        }

        let systemWide = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(systemWide, 0.5)  // 500ms cap to prevent hangs
        var focusedValue: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(
            systemWide,
            kAXFocusedUIElementAttribute as CFString,
            &focusedValue
        )
        guard status == .success, let focusedValue else {
            return FocusedElementSnapshot(
                bundleIdentifier: frontmostBundleID,
                role: nil,
                value: nil,
                isEditable: false,
                hasFocusedElement: false
            )
        }

        let element = unsafeDowncast(focusedValue, to: AXUIElement.self)
        AXUIElementSetMessagingTimeout(element, 0.5)
        let role = copyStringAttribute(kAXRoleAttribute as CFString, from: element)
        let value = copyStringAttribute(kAXValueAttribute as CFString, from: element)
        let isEditable =
            isAttributeSettable(kAXSelectedTextRangeAttribute as CFString, on: element)
            || isAttributeSettable(kAXValueAttribute as CFString, on: element)
            || [
            kAXTextFieldRole as String,
            kAXTextAreaRole as String,
            kAXComboBoxRole as String,
            "AXSearchField",
        ].contains(role)

        return FocusedElementSnapshot(
            bundleIdentifier: frontmostBundleID,
            role: role,
            value: value,
            isEditable: isEditable,
            hasFocusedElement: true
        )
    }

    private func isAttributeSettable(_ attribute: CFString, on element: AXUIElement) -> Bool {
        var settable = DarwinBoolean(false)
        let status = AXUIElementIsAttributeSettable(element, attribute, &settable)
        return status == .success && settable.boolValue
    }

    private func copyStringAttribute(_ attribute: CFString, from element: AXUIElement) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else {
            return nil
        }
        return value as? String
    }

    private func inferInjectionOutcome(
        before: FocusedElementSnapshot?,
        after: FocusedElementSnapshot?,
        pastedText: String
    ) -> InjectionOutcome {
        guard let before, let after else {
            return .inserted
        }

        // No frontmost app → nothing to paste into (e.g. desktop)
        if before.bundleIdentifier == nil && after.bundleIdentifier == nil {
            return .copiedToClipboard
        }

        // If there's a frontmost app but AX couldn't find a focused element
        // (common with Electron apps like WeChat, Feishu/Lark, or AX timeout),
        // assume paste worked. Cmd+V is a system shortcut and almost always
        // reaches the active app regardless of AX visibility.
        if !before.hasFocusedElement || !after.hasFocusedElement {
            return .inserted
        }

        // Value changed → paste definitely worked (strongest signal)
        if let beforeValue = before.value, let afterValue = after.value, beforeValue != afterValue {
            return .inserted
        }

        // Either snapshot says editable → trust it
        if before.isEditable || after.isEditable {
            return .inserted
        }

        // Known non-editable roles with no value change → paste failed
        let nonEditableRoles: Set<String> = [
            kAXStaticTextRole as String,
            kAXImageRole as String,
            kAXGroupRole as String,
            kAXWindowRole as String,
            kAXButtonRole as String,
            kAXCheckBoxRole as String,
            kAXToolbarRole as String,
            kAXMenuBarRole as String,
            kAXMenuItemRole as String,
            kAXScrollBarRole as String,
            kAXSliderRole as String,
            kAXProgressIndicatorRole as String,
            kAXIncrementorRole as String,
            kAXBusyIndicatorRole as String,
            kAXRadioButtonRole as String,
            kAXPopUpButtonRole as String,
            kAXColorWellRole as String,
            kAXRelevanceIndicatorRole as String,
            kAXLevelIndicatorRole as String,
            kAXCellRole as String,
            kAXLayoutAreaRole as String,
            kAXRowRole as String,
            kAXColumnRole as String,
            kAXOutlineRole as String,
            kAXTableRole as String,
            kAXBrowserRole as String,
            kAXSplitGroupRole as String,
        ]
        if let role = after.role, nonEditableRoles.contains(role),
           before.value == after.value {
            return .copiedToClipboard
        }

        // Default: assume success (covers Electron/Gecko/CEF with nil/unknown roles)
        return .inserted
    }


}
