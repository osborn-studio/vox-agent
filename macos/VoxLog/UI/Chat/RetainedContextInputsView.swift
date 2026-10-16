import AppKit
import SwiftUI

/// Raw text review only. Copying never submits, retries, changes a project or deletes.
struct RetainedContextInputsView: View {
    let inputs: [RetainedContextInput]
    var page: RetainedContextPage? = nil
    var error: String = ""
    var previous: () -> Void = {}
    var next: () -> Void = {}
    var onCopyRefused: () -> Void = {}
    var mayCopy: (RetainedContextInput) -> Bool = { _ in false }
    @State private var expanded = false
    @State private var copyRefused = false

    var body: some View {
        if !inputs.isEmpty || page != nil || !error.isEmpty {
            DisclosureGroup(L("待核对原文（每页20条）", "Retained input review (20 per page)"), isExpanded: $expanded) {
                if copyRefused { Text(L("复制未执行，原剪贴板未改变；请重新核对保留原文。", "Copy refused; clipboard unchanged. Review retained originals again.")).font(.caption) }
                if !error.isEmpty { Text(verbatim: error).font(.caption) }
                if let page {
                    Text(verbatim: L("当前显示", "Showing ") + " \(page.items.isEmpty ? 0 : page.offset + 1)–\(page.offset + page.items.count) / \(page.total)")
                        .font(.caption)
                    HStack {
                        Button(L("上一页", "Previous page"), action: previous).disabled(page.offset == 0)
                        Button(L("下一页", "Next page"), action: next).disabled(page.nextOffset == nil)
                    }
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: TF.spacingSM) {
                        Text(L("这是核对时的只读快照，可再次点击核对刷新。复制不会重发；结果未知或编号冲突时，先核对服务端，避免重复发送。", "Read-only review snapshot; review again to refresh. Copy never resends. Check unknown/conflicting records on the server before submitting again."))
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(inputs, id: \.reviewKey) { input in
                            VStack(alignment: .leading, spacing: TF.spacingXS) {
                                HStack {
                                    Text(input.state.label).font(.caption)
                                    Spacer()
                                    Button {
                                        guard mayCopy(input) else { copyRefused = true; onCopyRefused(); return }
                                        copyRefused = false
                                        NSPasteboard.general.clearContents()
                                        NSPasteboard.general.setString(input.text, forType: .string)
                                    } label: {
                                        Label(L("复制原文", "Copy original"), systemImage: "doc.on.doc")
                                    }
                                    .accessibilityLabel(L("复制保留的原文，不重新发送", "Copy retained original without resending"))
                                }
                                Text(input.project?.repositoryID ?? L("未指定项目", "Unspecified project"))
                                    .font(.caption).foregroundStyle(.secondary)
                                Text(input.text).font(.body)
                                Text(input.id).font(.caption2).foregroundStyle(.secondary)
                            }
                            .accessibilityElement(children: .contain)
                            Divider()
                        }
                    }
                }
                .frame(maxHeight: 180)
            }
            .onChange(of: page?.snapshot) { copyRefused = false }
            .onChange(of: page?.offset) { copyRefused = false }
            .padding(.horizontal, TF.spacingMD).padding(.vertical, TF.spacingXS)
        }
    }
}
