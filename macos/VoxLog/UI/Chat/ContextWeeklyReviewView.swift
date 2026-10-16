import SwiftUI
import AppKit

/// Explicit reads and an in-memory editor; no send, export or backup action.
struct ContextWeeklyReviewView: View {
    let scope: () throws -> String
    let fetch: (String, String?) async throws -> Data
    @Environment(\.scenePhase) private var scenePhase
    @State private var review = ContextWeeklyReview()
    @State private var selectedDate = Date()
    @State private var expanded = false

    private var coverageLabel: String {
        switch review.coverage {
        case .notLoaded: return L(review.coverageDescription, "Sources not loaded; this does not mean no work.")
        case .loading: return L(review.coverageDescription, "Reading sources; coverage is not yet known.")
        case .failed: return L(review.coverageDescription, "Source read failed; weekly coverage cannot be determined.")
        case .partial: return L(review.coverageDescription, "Partial authorized sources; pages remain or records were excluded.")
        case .exhausted: return L(review.coverageDescription, "Authorized query exhausted; this is not a complete record of the work week.")
        }
    }

    var body: some View {
        DisclosureGroup(L("本人来源与周报草稿", "My sources and weekly draft"), isExpanded: $expanded) {
            ScrollView {
                VStack(alignment: .leading, spacing: TF.spacingSM) {
                    DatePicker(L("周内日期（上海时间）", "Date within week (Shanghai time)"), selection: $selectedDate, displayedComponents: .date)
                        .disabled(review.hasDraft)
                        .environment(\.timeZone, TimeZone(identifier: "Asia/Shanghai")!)
                    Text(verbatim: review.week + " · Asia/Shanghai")
                        .font(.caption).monospacedDigit()
                    Text(L("仅查看本人已接收的备份输入，不含私人或离线未提交记录。个人陈述不等于工作已完成；没有来源不等于没有工作。", "Only your accepted backups are covered, excluding private or unsubmitted offline records. Statements do not prove completed work; no sources does not mean no work."))
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button(L("重新加载并清空草稿", "Reload and clear draft")) {
                            review.reset(week: ContextWeeklyReview.monday(containing: selectedDate))
                            review.startLoad(fetch: fetch, scope: scope)
                        }.disabled(review.loading)
                        if review.hasMore && review.pages < ContextWeeklyReview.maximumPages {
                            Button(L("加载更多来源", "Load more sources")) {
                                review.startLoad(fetch: fetch, scope: scope)
                            }.disabled(review.loading || review.hasDraft)
                        }
                    }
                    if review.foregroundCleared {
                        Text(L("读取已取消，或因离开前台／关闭视图清空临时来源与草稿；请重新加载。", "Read cancelled or temporary sources and draft cleared after foreground or view loss; reload to continue."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if review.loading {
                        Text(L("正在读取本人来源…", "Reading your sources…"))
                        Button(L("取消读取并清空临时来源", "Cancel read and clear temporary sources")) { review.suspend() }
                    }
                    if review.failed { Text(L("来源读取失败或权限已变更，来源与草稿已清空。请重新加载。", "Source read failed or permissions changed; sources and draft cleared. Reload to retry.")) }
                    if review.pages > 0 && review.sources.isEmpty {
                        Text(L("本页没有可见来源；有下一页时仍可继续加载。", "No visible sources on this page; continue when another page is available."))
                    }
                    Text(L("已加载来源", "Loaded sources") + ": \(review.sources.count) · " + L("页数", "Pages") + ": \(review.pages)/\(ContextWeeklyReview.maximumPages)")
                        .font(.caption).monospacedDigit()
                    Text(coverageLabel).font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("weekly-source-coverage")
                    if review.hasMore && review.pages == ContextWeeklyReview.maximumPages {
                        Text(L("已到本机分页上限，不能视作完整周记录。", "Client page limit reached; this is not a complete weekly record."))
                    }
                    ForEach(review.notices, id: \.self) { Text(verbatim: $0).font(.caption).foregroundStyle(.secondary) }
                    if review.hasMore || review.omitted {
                        Text(L("来源不完整：尚有分页或部分记录被权限/有效性规则排除。", "Incomplete sources: more pages remain or records were excluded by permission/validity rules."))
                    }
                    if !review.projectCoverage.isEmpty {
                        Text(L("按明确归属统计已加载输入（不代表成果）", "Loaded input counts by explicit attribution (not outcomes)"))
                            .font(.caption).foregroundStyle(.secondary)
                        ForEach(review.projectCoverage) { group in
                            Text(verbatim: (group.id.project ?? L("未归属项目", "Unattributed project")) + " · " + (group.id.repository ?? L("未知仓库", "Unknown repository")) + " · \(group.count)")
                                .font(.caption).accessibilityElement(children: .combine)
                        }
                    }
                    ForEach(review.sources) { source in
                        VStack(alignment: .leading, spacing: TF.spacingXS) {
                            Toggle(L("选入核对草稿", "Include in review draft"), isOn: Binding(
                                get: { !review.excludedSourceIDs.contains(source.id) },
                                set: { review.selectSource(source.id, included: $0) }))
                                .disabled(review.loading || review.failed || review.hasDraft)
                                .accessibilityLabel(L("选入来源", "Include source") + " " + source.eventId)
                            VStack(alignment: .leading, spacing: TF.spacingXS) {
                            Text(verbatim: source.eventId + " · " + source.submittedAt).font(.caption)
                            Text(verbatim: source.targetId + " · " + source.deliveryStatus + " · " + (source.attribution?.repositoryId ?? "unknown repository")).font(.caption)
                            Text(verbatim: source.text)
                            }.accessibilityElement(children: .combine)
                        }
                        Divider()
                    }
                    Button(L("用已加载来源创建核对草稿", "Create review draft from loaded sources")) { review.createDraft() }
                        .disabled(review.loading || review.failed || review.selectedSources.isEmpty || review.hasDraft)
                    if review.draftLimit {
                        Text(L("草稿超过300000字元，此次编辑或生成未应用；原草稿与来源保留。", "Draft exceeds 300000 scalar values; edit or generation rejected, prior draft and sources retained."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    switch review.draftCopyStatus {
                    case .none: EmptyView()
                    case .copied: Text(L("已复制核对草稿；不会自动发送或备份。", "Review draft copied; no automatic send or backup."))
                    case .failed: Text(L("剪贴板写入失败；草稿保留，可重试。", "Clipboard write failed; draft retained, retry available."))
                    case .denied: Text(L("账号归属已变或无法核验，未复制；请重新加载。", "Identity changed or could not be verified; nothing copied. Reload sources."))
                    }
                    if review.hasDraft {
                        let editorEpoch = review.draftEpoch
                        Text(L("本机临时草稿：可编辑，不自动保存或发送。离开此页面或前台、切换账号、服务、周次或读取失败会清空。编辑期间暂停分页，重新加载会清空草稿。", "Temporary local draft: editable, never saved or sent automatically. Leaving this page or foreground, account, service or week changes and read failures clear it. Paging pauses while editing; reload clears the draft."))
                            .font(.caption).foregroundStyle(.secondary)
                        Button(L("复制核对草稿", "Copy review draft")) {
                            review.copyDraft(scope: scope) { text in
                                NSPasteboard.general.clearContents()
                                return NSPasteboard.general.setString(text, forType: .string)
                            }
                        }.disabled(review.loading || review.failed || review.draft.isEmpty)
                        Text(L("主动复制到系统剪贴板，供你自行使用；不代表成果已验证，也不会自动发布。", "Explicitly copies to the system clipboard for your use; outcomes remain unverified and nothing is published automatically."))
                            .font(.caption).foregroundStyle(.secondary)
                        Button(L("清空临时草稿，保留来源并重新选择", "Clear temporary draft, keep sources and reselect")) {
                            review.discardDraft()
                        }.disabled(review.loading || review.failed)
                        TextEditor(text: Binding(get: { review.draft }, set: { review.editDraft($0, epoch: editorEpoch) }))
                            .frame(minHeight: ChatLayoutMetrics.weeklyDraftEditorHeight)
                            .accessibilityLabel(L("本人周报核对草稿，未验证工作成果", "Personal weekly review draft, work outcomes unverified"))
                    }
                }.padding(TF.spacingSM)
            }.frame(maxHeight: ChatLayoutMetrics.weeklyReviewMaxHeight)
        }
        .padding(.horizontal, TF.spacingMD)
        .onAppear {
            if review.week.isEmpty { review.reset(week: ContextWeeklyReview.monday(containing: selectedDate)) }
        }
        .onChange(of: selectedDate) { _, date in
            let week = ContextWeeklyReview.monday(containing: date)
            if review.week != week { review.reset(week: week) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { review.suspend() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in review.suspend() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { _ in review.suspend() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didMiniaturizeNotification)) { _ in review.suspend() }
        .onChange(of: expanded) { _, value in if !value { review.suspend() } }
        .onDisappear { review.reset(week: "") }
    }
}
