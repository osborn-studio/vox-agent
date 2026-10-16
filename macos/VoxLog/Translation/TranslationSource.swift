import Foundation
import CryptoKit

/// A display-only snapshot. It deliberately has no task, send, history or backup handle.
struct TranslationSource: Equatable, Sendable {
    enum Kind: String, Sendable { case local, agent, feishu, original }
    let kind: Kind
    let id: String
    let scope: String
    let text: String
    let allowed: Bool
    var key: String { kind.rawValue + ":" + id }
    var version: Data { Data(SHA256.hash(data: Data(text.utf8))) }
}

enum TranslationFailure: Error, Equatable {
    case configuration, unsupported, denied, busy, inputLimit, outputLimit, incomplete
    case syntax, protection, empty, protocolError, network, timeout, cancelled
    var message: String {
        switch self {
        case .configuration: return L("请先配置 Polish 模型。", "Configure a Polish model first.")
        case .unsupported: return L("此模型的翻译协议尚未验证，请更换 Polish 设置。", "Translation protocol is unverified for this model. Change Polish settings.")
        case .denied: return L("此来源的模型翻译政策未开启。", "Model translation is not enabled for this source.")
        case .busy: return L("已有两条翻译正在执行，请稍后重试。", "Two translations are running. Try again later.")
        case .inputLimit: return L("整条原文超过本期 6,000 UTF-8 字节上限，请缩短文本。", "The entire source exceeds the current 6,000 UTF-8 byte limit. Shorten it.")
        case .outputLimit, .incomplete: return L("译文超出预算或未完整结束，请缩短文本或更换模型设置。", "Translation exceeded its budget or did not finish. Shorten the source or change model settings.")
        case .syntax: return L("代码定界符未闭合，无法可靠保护原文。", "Unclosed code delimiters prevent reliable protection.")
        case .protection: return L("译文改动了代码或链接，请重试。", "Translation changed code or links. Retry.")
        case .cancelled: return L("已取消", "Cancelled")
        default: return L("翻译失败，请重试。", "Translation failed. Retry.")
        }
    }
}
