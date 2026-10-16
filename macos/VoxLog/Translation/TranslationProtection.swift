import Foundation

/// Byte-oriented, conservative parser; never normalizes Unicode or repairs output.
/// Fences are line-start runs (up to three spaces), closing runs must be at least
/// as long. The newline after the closing delimiter is an outside separator.
struct TranslationProtection {
    let pieces: [Data: Int]
    let onlyCode: Bool

    init(_ text: String) throws {
        let bytes = Array(text.utf8)
        var covered = Array(repeating: false, count: bytes.count)
        var pieces: [Data: Int] = [:]
        var codeCount = 0
        var urlCount = 0
        func add(_ range: Range<Int>, code: Bool) {
            pieces[Data(bytes[range]), default: 0] += 1
            for i in range { covered[i] = true }
            if code { codeCount += 1 } else { urlCount += 1 }
        }
        func endLine(_ i: Int) -> Int {
            var j = i
            while j < bytes.count && bytes[j] != 10 { j += 1 }
            return j
        }
        func marker(_ start: Int) -> (Int, Int, UInt8)? {
            var i = start
            while i < bytes.count && bytes[i] == 32 && i - start < 3 { i += 1 }
            guard i < bytes.count, bytes[i] == 96 || bytes[i] == 126 else { return nil }
            let b = bytes[i]; var j = i
            while j < bytes.count && bytes[j] == b { j += 1 }
            return j - i >= 3 ? (i, j, b) : nil
        }
        var line = 0
        while line < bytes.count {
            let end = endLine(line)
            if let (start, after, b) = marker(line) {
                if b == 96 && bytes[after..<end].contains(96) { throw TranslationFailure.syntax }
                var next = min(end + 1, bytes.count), closing: Int?
                while next < bytes.count {
                    let e = endLine(next)
                    if let (s, a, c) = marker(next), c == b, a - s >= after - start,
                       bytes[a..<e].allSatisfy({ $0 == 32 || $0 == 9 || $0 == 13 }) {
                        closing = e; break
                    }
                    next = min(e + 1, bytes.count)
                }
                guard let closing else { throw TranslationFailure.syntax }
                add(line..<closing, code: true); line = min(closing + 1, bytes.count)
            } else { line = min(end + 1, bytes.count) }
        }
        var i = 0
        while i < bytes.count {
            if covered[i] || bytes[i] != 96 { i += 1; continue }
            let start = i
            while i < bytes.count && bytes[i] == 96 && !covered[i] { i += 1 }
            let length = i - start
            var j = i, close: Int?
            while j < bytes.count && !covered[j] {
                if bytes[j] == 96 {
                    let s = j
                    while j < bytes.count && !covered[j] && bytes[j] == 96 { j += 1 }
                    if j - s == length { close = j; break }
                } else { j += 1 }
            }
            guard let close else { throw TranslationFailure.syntax }
            add(start..<close, code: true); i = close
        }
        // Scan remaining Unicode scalars for URL boundaries, retaining original bytes.
        let boundaries = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: "()[]{}<>\"'`，。！？；：、（）【】《》“”‘’"))
        i = 0
        while i < bytes.count {
            let http = Array("http://".utf8), https = Array("https://".utf8)
            guard !covered[i], bytes[i...].starts(with: http) || bytes[i...].starts(with: https) else { i += 1; continue }
            let start = i
            while i < bytes.count && !covered[i] {
                let first = bytes[i]
                let width = first < 128 ? 1 : first < 224 ? 2 : first < 240 ? 3 : 4
                guard i + width <= bytes.count,
                      let scalar = String(decoding: bytes[i..<i + width], as: UTF8.self).unicodeScalars.first,
                      !boundaries.contains(scalar) else { break }
                i += width
            }
            var end = i
            while end > start && [UInt8(46), 44, 33, 63, 59, 58].contains(bytes[end - 1]) { end -= 1 }
            if end > start { add(start..<end, code: false) }
            if i == start { i += 1 }
        }
        // Feishu mention placeholders and complete at-tags remain literal data.
        i = 0
        let mention = Array("@_user_".utf8), at = Array("<at ".utf8), closeAt = Array("</at>".utf8)
        while i < bytes.count {
            if covered[i] { i += 1; continue }
            if bytes[i...].starts(with: mention) {
                var end = i + mention.count
                while end < bytes.count && (48...57).contains(bytes[end]) { end += 1 }
                if end > i + mention.count { add(i..<end, code: false); i = end; continue }
            } else if bytes[i...].starts(with: at) {
                var end = i + at.count
                while end < bytes.count && !bytes[end...].starts(with: closeAt) { end += 1 }
                guard end < bytes.count else { throw TranslationFailure.syntax }
                end += closeAt.count
                guard (i..<end).allSatisfy({ !covered[$0] }) else { throw TranslationFailure.syntax }
                add(i..<end, code: false); i = end; continue
            }
            i += 1
        }
        for tag in ["<think>", "</think>"] {
            let marker = Array(tag.utf8)
            for start in bytes.indices where !covered[start] && bytes[start...].starts(with: marker) {
                add(start..<(start + marker.count), code: false)
            }
        }
        self.pieces = pieces
        self.onlyCode = codeCount > 0 && urlCount == 0 && bytes.indices.allSatisfy { covered[$0] || [9, 10, 13, 32].contains(bytes[$0]) }
    }

    func validate(_ output: String) throws {
        guard let parsed = try? TranslationProtection(output), parsed.pieces == pieces else {
            throw TranslationFailure.protection
        }
    }
}
