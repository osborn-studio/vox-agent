import SwiftUI

struct ContextPrivateInboxView: View {
    let store: ContextPrivateDraftStore
    let scope: () throws -> String
    let currentScope: () -> String?
    let request: ContextPrivateInbox.Request
    @Environment(\.scenePhase) private var scenePhase
    @State private var inbox: ContextPrivateInbox
    init(store: ContextPrivateDraftStore, scope: @escaping () throws -> String, request: @escaping ContextPrivateInbox.Request, currentScope: @escaping () -> String? = { nil }, inbox: ContextPrivateInbox? = nil) {
        self.store = store; self.scope = scope; self.request = request; self.currentScope = currentScope
        _inbox = State(initialValue: inbox ?? ContextPrivateInbox(requestStore: ContextPrivateRequestStore(), inputStore: ContextPrivateInputStore()))
    }
    private func archiveKeyMessage(_ state: ContextPrivateRequestStore.ArchiveKeyState) -> String {
        switch state {
        case .present: return L("读取到32字节钥匙；未验证归档内容，不证明可恢复。", "Read a 32-byte key; archive contents unverified, recovery not established.")
        case .missing: return L("本应用查询未找到归档钥匙；不证明已删除，未创建或替换，文件保留。", "Archive key not found for this app query; not evidence of deletion. No key created or replaced; files retained.")
        case .invalid: return L("归档钥匙格式无效；未替换钥匙，文件保留。", "Archive key format invalid; no replacement, files retained.")
        case .unavailable: return L("归档或钥匙不可核验（可能被锁定或拒绝访问）；不表示钥匙丢失，文件保留。", "Archive or key cannot be checked (possibly locked or denied); not evidence of key loss, files retained.")
        }
    }
    @State private var expanded = false
    @State private var withdrawalReview = false
    @State private var discardReview = false

    private var inputSnapshotStatus: String {
        switch inbox.inputSavedStatus {
        case "saved": return L("输入已加密保存；未发送。", "Input saved encrypted; nothing sent.")
        case "hidden": return L("快照正文仍隐藏，恢复需联网只读核验。", "Snapshot remains hidden; restoration requires online read-only verification.")
        case "restored": return L("已恢复未提交输入；不会自动发送。", "Unsubmitted input restored; never sent automatically.")
        case "not-found": return L("当前身份没有输入快照。", "No input snapshot for this identity.")
        case "pending": return L("当前身份有未确认或已归档未知请求；请先核对原请求，恢复暂停。", "This identity has an unconfirmed or archived unknown request; verify it first. Snapshot restoration is paused.")
        case "capacity": return L("本机输入快照容量已满；当前编辑和原密文保留，未发送或自动清理。", "Local input snapshot capacity reached; current editor and original ciphertext retained. Nothing sent or cleaned automatically.")
        case "durability": return L("新密文可能已安装，但持久写入未确认；未发送，请核对存储后重新保存。", "New ciphertext may be installed, but durability is unconfirmed. Nothing sent; check storage before saving again.")
        case "submitted": return L("此快照已关联提交请求；请核对原请求，不能作为新记录恢复。", "Snapshot is linked to a submission; verify the original request rather than restoring as a new record.")
        default: return L("保存或恢复未确认，现有密文未自动删除；请核对身份、钥匙串、原修订和存储。", "Save or restore unconfirmed; existing ciphertext never deleted automatically. Check identity, Keychain, original revision and storage.")
        }
    }
    var body: some View {
        DisclosureGroup(L("私人 Context 收件箱", "Private Context inbox"), isExpanded: $expanded) {
            ScrollView {
                VStack(alignment: .leading, spacing: TF.spacingSM) {
                    Text(L("仅本人可见，不投递飞书。来源核对失败会隐藏未提交编辑，可同身份复核后恢复。服务端需管理员单独启用；当前复用已配置的团队服务与登录凭据。显示最近七天的创建记录，个人陈述不证明代码成果。", "Visible only to you, never delivered to Feishu. Failed source checks hide unsaved edits; same-identity revalidation can restore them. Requires separate server activation; uses your configured team service and credential. Shows records created in the last seven days; statements do not prove code outcomes."))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(L("未保存输入仍只保留在内存；提交前请求加密保留，未知结果不自动重放。离开前台清空可见文字；恢复需同身份联网核对。", "Unsaved input remains in memory. Requests persist encrypted before submission; unknown outcomes never replay automatically. Leaving foreground clears visible text; recovery requires same-identity online verification."))
                        .font(.caption).foregroundStyle(.secondary)
                    Button(L("离线加密保存当前输入（替换本身份上次输入快照）", "Save current input encrypted offline (replace this identity's previous snapshot)")) { inbox.saveInputOffline(scope: scope) }
                        .disabled(!inbox.canSaveInput)
                    Button(L("离线读取隐藏的输入快照", "Read hidden input snapshot offline")) { inbox.stageInputOffline(scope: scope) }
                        .disabled(!inbox.canStageInput)
                    if inbox.hasStagedInput {
                        Button(L("只读核验身份与原修订后恢复输入", "Verify identity and original revision read-only, then restore input")) { Task { await inbox.restoreInput(request: request, scope: scope) } }
                            .disabled(inbox.busy || !inbox.input.isEmpty || inbox.hasDraft)
                    }
                    if !inbox.inputSavedStatus.isEmpty {
                        Text(inputSnapshotStatus).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("privateContext.inputSnapshotStatus")
                    }
                    Button(L("离线读取隐藏的未确认请求", "Read encrypted pending request offline")) { inbox.stageSavedRequest(scope: scope) }
                        .disabled(!inbox.canStageSavedRequest)
                    DisclosureGroup(L("归档信息与显式只读核验", "Archive metadata and explicit read-only checks")) {
                        Text(L("默认只列当前账号的文件标签和大小；内容认证需单独点击，不恢复或发送。文件存在不证明飞书已收到；标签不是已验证的内容校验和。", "Lists only this account's file labels and sizes by default; content authentication requires a separate click, never restore or send. A file is not proof of delivery, and its label is not a verified content checksum."))
                            .font(.caption).foregroundStyle(.secondary)
                        if inbox.archiveInspectionInFlight {
                            Text(L("后台只读检查尚未结束；若出现钥匙串解锁提示，请完成或拒绝。检查结束前暂停新的检查，未恢复或发送。", "A background read-only check is still running. Complete or deny any Keychain unlock prompt; new checks pause until it finishes. No restore or send."))
                                .font(.caption).foregroundStyle(.secondary)
                                .accessibilityIdentifier("privateContext.archiveInspectionInFlight")
                        }
                        Button(L("只读查看归档文件", "Inspect archive files read-only")) {
                            inbox.browseArchiveFiles(scope: {
                                guard let owner = currentScope() else { throw ContextPrivateInbox.Failure.invalid }
                                return owner
                            })
                        }
                            .disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight)
                            .accessibilityIdentifier("privateContext.browseArchiveFiles")
                        if inbox.archiveFilesStatus == "loaded", let owner = currentScope(), inbox.archiveFilesAreCurrent(scope: { owner }) {
                            if inbox.archiveFiles.isEmpty { Text(L("本次有限扫描未发现当前账号的匹配文件。", "No matching files for this account in this bounded scan.")).font(.caption).foregroundStyle(.secondary) }
                            if !inbox.archiveFiles.isEmpty {
                                Button(L("检查归档钥匙（可能需解锁，不解密）", "Check archive key (may require unlock; no decryption)")) {
                                    Task { await inbox.inspectArchiveKey(scope: { guard let owner = currentScope() else { throw ContextPrivateInbox.Failure.invalid }; return owner }) }
                                }.disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight)
                                    .accessibilityIdentifier("privateContext.inspectArchiveKey")
                                if let keyState = inbox.archiveKeyStatus {
                                    Text(archiveKeyMessage(keyState)).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            ForEach(inbox.archiveFiles) { file in
                                VStack(alignment: .leading) {
                                    Text("\(file.id.prefix(16))… · \(file.bytes) " + L("字节", "bytes"))
                                        .font(.caption.monospaced())
                                        .accessibilityLabel("\(file.id) · \(file.bytes) " + L("字节", "bytes"))
                                    Button(L("核对文件指纹（不解密）", "Check file fingerprint (no decryption)")) {
                                        Task { await inbox.inspectArchiveFingerprint(label: file.id, scope: { guard let owner = currentScope() else { throw ContextPrivateInbox.Failure.invalid }; return owner }) }
                                    }.disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight)
                                        .accessibilityLabel(L("核对归档文件指纹：", "Check archive file fingerprint: ") + file.id)
                                        .accessibilityIdentifier("privateContext.archiveFingerprint." + file.id)
                                    Button(L("认证归档内容（可能需解锁，不恢复）", "Authenticate archive contents (may require unlock; no restore)")) {
                                        Task { await inbox.authenticateArchive(label: file.id, scope: { guard let owner = currentScope() else { throw ContextPrivateInbox.Failure.invalid }; return owner }) }
                                    }.disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight || inbox.archiveAuthenticationStatus[file.id] == "checking")
                                        .accessibilityLabel(L("只读认证归档内容：", "Authenticate archive contents read-only: ") + file.id)
                                        .accessibilityIdentifier("privateContext.archiveAuthentication." + file.id)
                                    if let status = inbox.archiveAuthenticationStatus[file.id] {
                                        Text(status == "checking" ? L("正在后台核验；未恢复或发送。", "Checking in background; no restore or send.") : status == "authenticated" ? L("本次文件快照的加密认证和请求结构通过；不证明已投递，未恢复或发送。", "This file snapshot passed encryption authentication and request validation; not proof of delivery. No restore or send.") : L("无法认证：密钥、文件或内容不可安全核验；原归档保留。", "Cannot authenticate key, file or contents safely; original archive retained."))
                                            .font(.caption).foregroundStyle(.secondary)
                                            .accessibilityIdentifier("privateContext.archiveAuthenticationStatus." + file.id)
                                    }
                                    if let status = inbox.archiveFingerprintStatus[file.id] {
                                        Text(status == "matches" ? L("本次核对的文件指纹与标签一致；不证明加密认证、防篡改、内容有效或已投递。", "Checked file snapshot fingerprint matches its label; not encryption authentication, tamper evidence, valid contents or delivery.") : L("本次核对的文件指纹与标签不一致；原文件保留，请联系管理员。", "Checked file snapshot fingerprint differs from its label; original retained. Contact administrator."))
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                }.accessibilityElement(children: .contain)
                            }
                        } else if inbox.archiveFilesStatus == "loaded" {
                            Text(L("当前账号范围暂不可核验；归档文件保留，请稍后重新查看。", "Current account scope cannot be verified; archive files retained. Inspect again later."))
                                .font(.caption).foregroundStyle(.secondary)
                        } else if inbox.archiveFilesStatus == "capacity" {
                            Text(L("归档目录超过只读扫描限制；原文件保留，未清理。", "Archive directory exceeds read-only scan limits; original files retained, no cleanup."))
                                .font(.caption).foregroundStyle(.secondary)
                        } else if inbox.archiveFilesStatus == "unavailable" {
                            Text(L("归档信息不可安全读取或账号已变化；原文件保留。", "Archive metadata cannot be read safely or the account changed; original files retained."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if inbox.hasStagedRequest {
                        Text(L("原文仍隐藏；联网核对不会发送原请求。", "Original text remains hidden; online verification does not submit it."))
                        Button(L("联网核对原请求", "Verify original request online")) { Task { await inbox.restoreSavedRequest(request: request, scope: scope) } }
                            .disabled(inbox.busy || inbox.writeInFlight || !inbox.input.isEmpty || inbox.hasDraft)
                        if !inbox.input.isEmpty || inbox.hasDraft { Text(L("先保留或清空当前编辑，再恢复旧请求。", "Preserve or clear the current editor before restoring an earlier request.")) }
                    }
                    if inbox.canReviewArchive {
                        Button(L("核对归档原请求并放行新输入", "Review archiving original request to release new input")) { inbox.reviewArchive(scope: scope) }
                    }
                    if let review = inbox.archiveReview {
                        Text(L("服务端可能已收到此请求。归档只保留本机密文并放行新输入；不代表失败、撤回或已确认。重新创建同一内容可能重复。", "The server may already have received this request. Archiving preserves local ciphertext and releases new input; it does not mean failure, withdrawal or confirmation. Recreating the same content may duplicate it."))
                        Text(L("原身份范围 / 请求指纹", "Original identity scope / request fingerprint") + ": \(review.scope.prefix(12)) / \(review.eventFingerprint.prefix(12))")
                            .font(.caption).monospacedDigit()
                        Button(L("保留密文并确认放行", "Preserve ciphertext and confirm release")) { inbox.confirmArchive(scope: scope) }.disabled(!inbox.canReviewArchive)
                        Button(L("取消归档核对", "Cancel archive review")) { inbox.cancelArchiveReview() }
                    }
                    if inbox.archiveUndo != nil {
                        Button(L("原请求未被替换时撤销放行", "Undo release while original request is still active")) { inbox.undoArchive(scope: scope) }
                            .disabled(inbox.busy || inbox.writeInFlight || !inbox.input.isEmpty || inbox.hasDraft || inbox.hasUnsubmittedRecovery)
                    }
                    if inbox.requestRecoveryStatus == "archived" {
                        Text(L("原请求密文已归档；结果仍未知。允许新输入，但不要无意重复创建。", "Original ciphertext archived; outcome remains unknown. New input is allowed; avoid unintentionally recreating it."))
                    } else if inbox.requestRecoveryStatus == "archive-held" {
                        Text(L("已撤销放行；请显式重新读取原请求。未发送或撤回任何服务端记录。", "Release undone; explicitly reload the original request. No server record was sent or withdrawn."))
                    } else if inbox.requestRecoveryStatus == "archive-failed" {
                        Text(L("归档或撤销未确认；原文件保留。请核对原身份、钥匙串、存储容量和持久写入。", "Archive or undo unconfirmed; original files retained. Check original identity, Keychain, capacity and durable storage."))
                    } else if inbox.requestRecoveryStatus == "capacity" {
                        Text(L("本机请求存储容量已满；当前编辑和原文件保留，发送暂停。不会自动清理或重放。", "Local request storage capacity reached; current editor and original files retained, sending paused. No automatic cleanup or replay."))
                            .font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("privateContext.requestCapacityStatus")
                    } else if inbox.requestRecoveryStatus == "durability" {
                        Text(L("无法确认文件夹持久写入；加密请求保留，发送暂停。请核对存储卷后再恢复。", "Directory durability could not be confirmed; the encrypted request is retained and sending paused. Check the storage volume before recovery."))
                            .font(.caption).foregroundStyle(.secondary)
                    } else if inbox.requestRecoveryStatus == "invalid-receipt" {
                        Text(L("回执与原请求不一致或身份核验失败；原文和加密记录保留，不重发。", "Receipt does not match the original request or identity validation failed; text and encrypted records retained, no resend."))
                    } else if !inbox.requestRecoveryStatus.isEmpty {
                        Text(inbox.requestRecoveryStatus == "confirmed" ? L("原请求已确认并在本机落盘。", "Original request confirmed and saved locally.") : L("请求恢复尚未确认；损坏或钥匙串不可用时保留加密文件。", "Request recovery is unconfirmed; encrypted files are retained on corruption or unavailable Keychain."))
                    }
                    TextField(L("正文搜索（不放入网址）", "Text search (never in URL)"), text: Binding(get: { inbox.query }, set: { inbox.setQuery($0) })).id(inbox.inputEditorEpoch).disabled(inbox.busy || inbox.hasDraft)
                    if inbox.queryLimit { Text(L("搜索超过128字元，此次修改未应用。", "Search exceeds128 scalar values; edit rejected.")) }
                    Picker(L("筛选标签", "Filter tag"), selection: $inbox.filterTag) {
                        Text(L("全部", "All")).tag("")
                        ForEach(ContextPrivateSource.allowedTags, id: \.self) { Text(verbatim: $0).tag($0) }
                    }.disabled(inbox.busy || inbox.hasDraft)
                    HStack {
                        Button(L("重新读取并清空草稿", "Reload and clear draft")) {
                            withdrawalReview = false
                            Task { await inbox.load(request: request, scope: scope) }
                        }.disabled(inbox.busy)
                        if inbox.hasMore && inbox.pages < 5 {
                            Button(L("读取下一页", "Read next page")) { Task { await inbox.load(more: true, request: request, scope: scope) } }
                                .disabled(inbox.busy || inbox.hasDraft || inbox.searchChanged)
                        }
                    }
                    if inbox.searchChanged { Text(L("搜索条件已改变，重新读取以应用。", "Search changed; reload to apply.")) }
                    if inbox.hasUnsubmittedRecovery {
                        Text(L("未提交编辑暂存在此页面内存，保存已暂停；同身份重新核对原来源后可恢复。来源改版、撤回或撤权时不能恢复到旧修订。关闭此页仍会丢失暂存。", "Unsaved edits remain hidden in this page's memory; saving is paused. Restore only after same-identity source revalidation; revised, withdrawn or revoked sources cannot restore the old revision. Closing loses this recovery."))
                        Button(L("重新核对并恢复未提交编辑", "Revalidate and restore unsaved edits")) {
                            Task { await inbox.recoverUnsubmitted(request: request, scope: scope) }
                        }.disabled(!inbox.canRecoverUnsubmitted)
                        if !inbox.input.isEmpty || !inbox.inputTag.isEmpty {
                            Text(L("当前编辑已有新文字或标签；先另行保留或清空，再恢复暂存输入。", "The current editor has new text or a tag; preserve it separately or clear it before restoring hidden input."))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if inbox.writeInFlight { Text(L("写请求尚在处理，暂不能重试或新建。", "Write still in flight; retry and new writes are paused.")) }
                    if inbox.busy { Text(L("正在核对私人来源…", "Checking private sources…")) }
                    if let status = inbox.serverReplyStatus {
                        Text(L("本次HTTP回复", "Observed HTTP response") + ": \(status)" + (inbox.serverReplyCode.map { " · " + $0 } ?? ""))
                            .font(.caption).monospacedDigit()
                        Text(L("回复不证明此前未知请求未提交；原请求仍保留，请核对或显式归档。", "This response does not prove an earlier unknown request did not commit. The original remains; verify or explicitly archive it."))
                    }
                    if inbox.serverReplyCode == "PRIVATE_CONTEXT_PROJECT_CHANGED" {
                        Text(L("项目对应的仓库已变更；原文和原归属保留。请核对原请求，不会自动改绑或作为无项目重发。", "Project repository changed; original text and attribution retained. Verify the original request; never rebound or resent as unassigned automatically."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if inbox.failed {
                        Text(L("请求失败或服务未启用；来源核对失败会使来源与草稿失效。未提交编辑恢复及未确认写入请按页面提示处理。", "Request failed or service disabled; failed source checks invalidate sources and drafts. Follow the recovery and unconfirmed-write notices for your local edits."))
                    }
                    if inbox.writeUncertain {
                        Text(L("写入结果未确认，未自动重试。创建按唯一事件核对；编辑和撤回按独立请求编号核对历史回执，相同内容本身不作证明。未找到回执也不会重发。历史仍保留。", "Write outcome unconfirmed; no automatic retry. Creation is checked by unique event; edits and withdrawals require their distinct historical request receipt. Matching content alone is not proof. Missing receipts never trigger replay. History remains."))
                    }
                    if inbox.writeUncertain {
                        Button(L("只读核对未确认请求", "Verify unconfirmed request read-only")) {
                            Task { await inbox.retryUncertain(request: request, scope: scope) }
                        }.disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight)
                    }
                    if inbox.writeUncertain {
                        Button(L("核对隐藏未确认请求", "Review hiding unconfirmed request")) { discardReview = true }.disabled(inbox.busy)
                        if discardReview {
                            Text(L("只隐藏页面内存，磁盘原请求仍保留；未核对确认或显式归档前，新请求会被阻止。不会撤回服务器记录。", "Only hides page memory. The encrypted original remains and blocks new requests until verified or explicitly archived. Server records are not withdrawn."))
                            Button(L("隐藏页面中的未确认请求", "Hide unconfirmed request in this page")) { inbox.discardUncertain(); discardReview = false }
                                .disabled(inbox.busy)
                        }
                    }
                    Text(L("已加载", "Loaded") + ": \(inbox.sources.count) · \(inbox.pages)/5")
                        .font(.caption).monospacedDigit()
                    if inbox.hasMore || inbox.omitted { Text(L("有未加载或被排除的来源，不能视为完整记录。", "Unloaded or excluded sources remain; this is not a complete record.")) }
                    ForEach(inbox.sources) { source in
                        VStack(alignment: .leading, spacing: TF.spacingXS) {
                            Text(verbatim: source.createdAt + " · revision \(source.revision)").font(.caption).monospacedDigit()
                            Text(verbatim: (source.tags ?? []).joined(separator: ", ") + " · " + (source.repositoryId ?? "unassigned")).font(.caption)
                            Text(verbatim: source.text ?? "")
                            Button(L("替换当前编辑为此来源", "Replace editor with this source")) { inbox.select(source); withdrawalReview = false }
                                .disabled(inbox.busy || inbox.writeUncertain)
                        }.accessibilityElement(children: .contain)
                        Divider()
                    }
                    HStack {
                        Text(inbox.editing == nil ? L("新增私人输入", "New private input") : L("编辑已选来源（保留项目）", "Edit selected source (preserve project)"))
                        Button(L("改为新输入（丢弃本机编辑与暂存）", "Start new input (discard local editor and recovery)")) { inbox.newInput(); withdrawalReview = false }
                            .disabled(inbox.busy || inbox.writeUncertain)
                    }
                    if inbox.editing == nil {
                        Button(L("读取可选项目（只读）", "Load available projects (read-only)")) { Task { await inbox.loadProjects(request: request, scope: scope) } }.disabled(!inbox.canChooseProject)
                        Picker(L("项目归属", "Project attribution"), selection: Binding(get: { inbox.selectedProject?.id ?? "" }, set: { inbox.chooseProject($0, scope: scope) })) {
                            Text(L("未归属项目", "Unassigned project")).tag("")
                            ForEach(inbox.projects) { Text(verbatim: $0.name + " · " + $0.repositoryId).tag($0.id) }
                        }.disabled(!inbox.canChooseProject).accessibilityIdentifier("privateContext.projectPicker")
                        if inbox.inputAttribution.frozen {
                            Text(L("输入归属已冻结：", "Input attribution frozen: ") + (inbox.inputAttribution.project?.repositoryID ?? L("未归属项目", "Unassigned project"))).font(.caption).foregroundStyle(.secondary)
                        }
                        if inbox.projectStatus == "unavailable" { Text(L("项目读取或身份核验失败；原文保留，不自动改变归属。", "Project or identity check failed; text retained, attribution never changed automatically.")) }
                    }
                    TextEditor(text: Binding(get: { inbox.input }, set: { inbox.setInput($0, scope: scope) })).id(inbox.inputEditorEpoch)
                        .frame(height: ChatLayoutMetrics.privateInputEditorHeight)
                        .accessibilityLabel(L("私人输入原文", "Private input text"))
                        .disabled(inbox.busy || inbox.writeUncertain)
                    if inbox.inputLimit { Text(L("输入超过8192字元，此次编辑未应用，原文字保留。", "Input exceeds8192 scalar values; edit was rejected and prior text retained.")) }
                    Picker(L("标签（其他现有标签保持不变，改选将替换）", "Tag (unchanged selection preserves existing tags; changing replaces them)"), selection: Binding(get: { inbox.inputTag }, set: { inbox.setInputTag($0, scope: scope) })) {
                        Text(L("无标签", "No tag")).tag("")
                        ForEach(ContextPrivateSource.allowedTags, id: \.self) { Text(verbatim: $0).tag($0) }
                    }.disabled(inbox.busy || inbox.writeUncertain)
                    Button(inbox.editing == nil ? L("保存私人输入", "Save private input") : L("保存新修订", "Save new revision")) {
                        withdrawalReview = false
                        Task { await inbox.mutate(request: request, scope: scope) }
                    }.disabled(inbox.busy || inbox.writeUncertain || inbox.hasUnsubmittedRecovery || !ContextPrivateSource.validText(inbox.input))
                    if let editing = inbox.withdrawalReference {
                        if inbox.withdrawalOutsideLoadedPage {
                            Text(L("撤回对象来自此前选择，不代表仍在当前筛选/授权列表中；仅以显示的原编号及修订提交，版本变化会拒绝。", "Withdrawal uses a previous selection, not proof of presence in current filters/permissions. Only the shown original ID/revision is submitted; version changes are rejected."))
                        }
                        Button(L("核对撤回范围", "Review withdrawal scope")) { withdrawalReview = true }.disabled(inbox.busy)
                        if withdrawalReview {
                            Text(verbatim: L("只撤回当前本人来源，不删除历史及备份：", "Withdraw only this own source; history and backups remain: ") + editing.id + " · revision \(editing.revision)")
                            Button(L("确认撤回此来源", "Confirm withdrawal of this source")) {
                                withdrawalReview = false
                                Task { await inbox.mutate(withdraw: true, request: request, scope: scope) }
                            }.disabled(inbox.busy || inbox.writeUncertain)
                        }
                    }
                    Divider()
                    Text(L("加密本机草稿：每个账号/企业/服务范围保留最近一份，仅手动保存。离线载入不显示正文、不上传；须联网核验引用后恢复。", "Encrypted local draft: latest one per account/org/service scope, explicit saves only. Offline staging hides text and never uploads; online reference validation is required to restore."))
                        .font(.caption).foregroundStyle(.secondary)
                    Button(L("离线载入已存草稿（无当前草稿时，正文隐藏）", "Stage saved draft offline (when no live draft, text hidden)")) {
                        inbox.stageSavedDraft(store: store, scope: scope)
                    }.disabled(inbox.busy || inbox.writeInFlight || inbox.hasDraft)
                    if inbox.hasStagedDraft {
                        Button(L("联网核验并恢复已存草稿", "Validate online and restore saved draft")) {
                            Task { await inbox.revealSavedDraft(request: request, scope: scope) }
                        }.disabled(inbox.busy || inbox.writeInFlight || inbox.hasDraft)
                    }
                    if !inbox.savedStatus.isEmpty { Text(verbatim: inbox.savedStatus).font(.caption) }
                    Button(L("核验来源版本并生成临时草稿", "Check source revisions and create temporary draft")) {
                        Task { await inbox.reviewDraft(create: true, request: request, scope: scope) }
                    }.disabled(inbox.busy || inbox.sources.isEmpty || inbox.hasDraft || inbox.writeUncertain)
                    if inbox.draftTooLarge { Text(L("草稿超过300000字元，请缩小筛选或减少加载页数；来源仍保留。", "Draft exceeds300000 scalar values. Narrow filters or load fewer pages; sources retained.")) }
                    if inbox.hasDraft && inbox.draftValid {
                        Text(L("私人草稿：不自动保存或发布，每30秒核对来源；变化、撤权或核对失败会清空可见正文，离开前台立即清空；已保存的加密副本保留。系统复制仍可用，已复制文字无法收回。", "Private draft: not automatically saved or published. Rechecks sources every30 seconds; changes, revocation or failed checks clear visible text. Leaving foreground clears it immediately; saved encrypted copies persist. System copy remains available; copied text cannot be revoked."))
                            .font(.caption).foregroundStyle(.secondary)
                        TextEditor(text: Binding(get: { inbox.draft }, set: { inbox.setDraft($0) })).id(inbox.draftEditorEpoch).disabled(inbox.busy).frame(height: ChatLayoutMetrics.weeklyDraftEditorHeight)
                            .accessibilityLabel(L("私人来源临时核对草稿", "Temporary private source review draft"))
                        Button(L("核验并加密保存/覆盖本机最近草稿", "Validate and save/replace latest encrypted local draft")) {
                            Task { await inbox.saveDraft(store: store, request: request, scope: scope) }
                        }.disabled(inbox.busy || inbox.writeInFlight || inbox.archiveInspectionInFlight)
                        if inbox.draftLimit { Text(L("草稿超过300000字元，此次编辑未应用。", "Draft exceeds300000 scalar values; edit rejected.")) }
                        Button(L("立即重新核对来源", "Recheck sources now")) { Task { await inbox.reviewDraft(create: false, request: request, scope: scope) } }
                            .disabled(inbox.busy)
                    }
                }.padding(TF.spacingSM)
            }.frame(maxHeight: ChatLayoutMetrics.privateInboxMaxHeight)
        }
        .padding(.horizontal, TF.spacingMD)
        .task {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                if expanded && scenePhase == .active && inbox.hasDraft { await inbox.reviewDraft(create: false, request: request, scope: scope) }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            // Hide derivatives immediately on background, never show unverified text on return.
            if phase != .active { inbox.suspend(); withdrawalReview = false }
        }
        .onChange(of: expanded) { _, value in if !value { inbox.reset(); withdrawalReview = false; discardReview = false } }
        .onChange(of: currentScope()) { previous, current in
            guard inbox.reconcileDisplayIdentity(previous: previous, current: current) else { return }
            // Account/origin change hides volatile old-scope content; encrypted snapshots remain untouched.
            withdrawalReview = false; discardReview = false
        }
        .onDisappear { inbox.reset() }
    }
}
