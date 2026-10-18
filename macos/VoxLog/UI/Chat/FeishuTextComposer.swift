import SwiftUI

/// Isolated entry: optional voice controls produce drafts; only explicit send enqueues.
struct FeishuTextComposer: View {
    @Bindable var outbox: FeishuConversationOutbox
    let account: FeishuAccountClient
    var voiceControls: () -> AnyView = { AnyView(EmptyView()) }

    private func label(_ record: FeishuPendingText) -> String {
        if record.status == "unknown" && record.stopped {
            return L("结果不确定；自动重试已停止，保留原提交编号", "Delivery uncertain; automatic retries stopped. Original ID retained.")
        }
        if record.status == "pending" && !record.accepted {
            return L("已保存在本机，尚未获服务接收", "Saved locally; service acceptance pending")
        }
        return switch record.status {
        case "sent": L("已投递", "Delivered")
        case "pending": L("待投递（接收不代表送达）", "Pending — acceptance is not delivery")
        case "sending": L("投递中", "Sending")
        case "unknown": L("结果不确定；保留原提交编号", "Delivery uncertain; original submission ID retained")
        case "membership_lost": L("会话权限已撤销", "Conversation permission revoked")
        default: L("未投递", "Not delivered")
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: TF.spacingMD) {
            Label(L("个人频道为镜像，不提供 AI 回答。语音转写需确认后发送。", "The personal channel is a mirror, without AI replies. Review voice drafts before sending."), systemImage: "info.circle")
                .foregroundStyle(.secondary)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: TF.spacingMD) {
                    ForEach(outbox.visibleRecords.filter { !account.timeline.suppresses(event: $0.eventId, message: $0.messageId, sent: $0.status == "sent") }) { record in
                        HStack(alignment: .top, spacing: TF.spacingSM) {
                            Rectangle().fill(TF.accent).frame(width: TF.spacingXS / 2)
                            VStack(alignment: .leading, spacing: TF.spacingXS) {
                                HStack { Spacer(minLength: TF.spacingSM); CopyOriginalButton(text: record.text) }
                                Text(record.text).textSelection(.enabled)
                                Label(label(record), systemImage: record.status == "sent" ? "checkmark.circle" : "clock")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            Text(L("本机传输记录：", "Local transport records: ") + "\(outbox.records.count)/200")
                .font(.caption).monospacedDigit().foregroundStyle(.secondary)
            if outbox.capacityFull {
                Label(L("本机发送队列已满。请先处理未完成的消息；已结束的本机发送记录会自动整理。", "The local sending queue is full. Resolve unfinished messages first; completed local sending records are cleaned up automatically."), systemImage: "tray.full")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if outbox.storageBlocked {
                Label(L("存储结果不确定，已暂停新提交。重新读取并核对原队列；损坏文件不会被覆盖。", "Storage is uncertain; new submissions are blocked. Reload to reconcile the original queue; corrupt files are preserved."), systemImage: "externaldrive.badge.exclamationmark")
                    .font(.caption).foregroundStyle(.secondary)
                Button(L("重新读取队列", "Reload queue")) { outbox.reloadStorage() }.disabled(outbox.flushing)
            }
            if (outbox.failure != nil || outbox.hasRetryableOriginals) && !outbox.storageBlocked {
                Label(L("提交尚未完成。待发记录保留；请检查账户、服务或存储后重试。", "Submission is unresolved. Pending records are retained; check account, service or storage and retry."), systemImage: "exclamationmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
                Button(L("重试原提交", "Retry original submissions")) {
                    Task { await outbox.flush(using: account, retryNow: true) }
                }.disabled(outbox.flushing)
            }
            if outbox.destination?.capability == "text_send" {
                voiceControls()
                TextEditor(text: $outbox.draft)
                    .font(.body)
                    .frame(minHeight: ChatLayoutMetrics.inputMinHeight, maxHeight: ChatLayoutMetrics.inputMaxHeight)
                    .accessibilityLabel(L("飞书文字草稿", "Feishu text draft"))
                    .overlay(RoundedRectangle(cornerRadius: TF.cornerSM).stroke(TF.line))
                HStack {
                    Text(L("不支持真实 @；发送后目标固定。", "Mentions are plain text; sending freezes the destination."))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(L("发送文字", "Send text")) {
                        if outbox.enqueue() { Task { await outbox.flush(using: account) } }
                    }.disabled(!outbox.canSend)
                }
            } else {
                Label(L("此服务仅支持会话发现，尚未启用发送。", "This service supports discovery only; sending is unavailable."), systemImage: "info.circle")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// Account-scoped recovery remains visible after revoked navigation falls back to an agent.
struct FeishuDraftRecoveryView: View {
    @Bindable var outbox: FeishuConversationOutbox
    let account: FeishuAccountClient
    var body: some View {
        if !outbox.recoveredDrafts.isEmpty || !outbox.attentionRecords.isEmpty {
            VStack(alignment: .leading, spacing: TF.spacingSM) {
                ForEach(outbox.recoveredDrafts) { item in
                    HStack(spacing: TF.spacingSM) {
                        Label(L("原会话草稿已保留：", "Draft retained for original conversation: ") + item.name, systemImage: "doc.text")
                        Button(L("复制草稿", "Copy draft")) {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(item.text, forType: .string)
                        }
                        Button(L("丢弃", "Discard")) { outbox.discardRecovery(item.id) }
                    }.font(.caption)
                }
                if !outbox.attentionRecords.isEmpty {
                    DisclosureGroup(L("已停止或撤权的提交", "Stopped or revoked submissions") + " (\(outbox.attentionRecords.count))") {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: TF.spacingSM) {
                                ForEach(outbox.attentionRecords) { record in
                                    VStack(alignment: .leading, spacing: TF.spacingXS) {
                                        Label(record.status == "unknown"
                                            ? L("结果不确定；自动重试已停止", "Delivery uncertain; automatic retries stopped")
                                            : L("服务确认会话权限已撤销", "Service confirmed conversation permission revoked"), systemImage: "exclamationmark.circle")
                                        Text(record.eventId).font(.caption.monospaced()).textSelection(.enabled)
                                        Text(record.text).textSelection(.enabled)
                                    }
                                }
                            }
                        }.frame(maxHeight: ChatLayoutMetrics.inputMaxHeight)
                        if outbox.hasRetryableOriginals {
                            Button(L("核对原提交", "Check original submissions")) {
                                Task { await outbox.flush(using: account, retryNow: true) }
                            }.disabled(outbox.flushing)
                        }
                    }.font(.caption)
                }
            }.padding(TF.spacingMD)
        }
    }
}
