#if PHASE4_REPAIR_HARNESS
import Foundation
@MainActor final class RepairWire: FeishuSendingTransport {
    var conversationIdentity: FeishuConversationIdentity?
    var saved: [FeishuPendingText] = []
    var calls = 0
    var mode = "permission"
    var held: CheckedContinuation<FeishuSendReceipt, Error>?
    func response(_ r: FeishuPendingText) async throws -> FeishuSendReceipt {
        calls += 1
        precondition(saved.first(where: {$0.eventId == r.eventId})?.status == "unknown", "attempt marker must be durable before network")
        if mode == "wait" { return try await withCheckedThrowingContinuation { held = $0 } }
        if mode == "permission" { throw FeishuSendingFailure.permission }
        if mode == "conflict" { throw FeishuSendingFailure.conflict }
        if mode == "rejected" { throw FeishuSendingFailure.rejected }
        if mode == "offline" { throw FeishuSendingFailure.unavailable }
        if mode == "wrongIdentity" { return FeishuSendReceipt(eventId:"other", conversationId:r.conversationId, status:"sent", reason:"DELIVERED", messageId:"om_one") }
        if mode == "wrongStatus" { return FeishuSendReceipt(eventId:r.eventId, conversationId:r.conversationId, status:"forged", reason:"STATIC", messageId:nil) }
        if mode == "malformed" { return FeishuSendReceipt(eventId:r.eventId, conversationId:r.conversationId, status:"sent", reason:"DELIVERED", messageId:"invalid") }
        return FeishuSendReceipt(eventId:r.eventId, conversationId:r.conversationId, status:mode == "sent" ? "sent" : "pending", reason:"QUEUED", messageId:mode == "sent" ? "om_one" : nil)
    }
    func submitText(_ r: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt { try await response(r) }
    func textReceipt(_ r: FeishuPendingText, for identity: FeishuConversationIdentity) async throws -> FeishuSendReceipt { try await response(r) }
}
@main struct RepairHarness {
    @MainActor static func waitFor(_ predicate: () -> Bool) async {
        let deadline=Date().addingTimeInterval(3)
        while !predicate() && Date()<deadline { await Task.yield() }
        precondition(predicate(), "bounded wait")
    }
    @MainActor static func main() async throws {
        let a=FeishuConversationIdentity(origin:"https://fixture.test",appId:"cli_test",tenantKey:"team",userId:"alice")
        let b=FeishuConversationIdentity(origin:"https://fixture.test",appId:"cli_test",tenantKey:"team",userId:"bob")
        let c=FeishuConversation(id:"fc_dm",kind:"personal",name:"Mirror",capability:"text_send")
        for mode in ["permission","conflict","rejected","malformed","wrongIdentity","wrongStatus"] {
            for accepted in [false,true] {
                let wire=RepairWire();wire.conversationIdentity=a;wire.mode=mode
                let disk=FeishuOutboxPersistence(load:{ _ in wire.saved },save:{ _,rows in wire.saved=rows })
                let box=FeishuConversationOutbox(persistence:disk);box.bind(a);box.select(c);box.draft="synthetic"
                precondition(box.enqueue())
                if accepted { wire.mode="pending";await box.flush(using:wire);wire.mode=mode }
                await box.flush(using:wire,retryNow:true)
                precondition(box.records[0].status == "unknown")
                let calls=wire.calls;await box.flush(using:wire);precondition(calls==wire.calls)
                let reopened=FeishuConversationOutbox(persistence:disk);reopened.bind(a)
                precondition(reopened.records[0].status == "unknown" && reopened.records[0].eventId==box.records[0].eventId)
            }
        }
        print("PASS: durable attempted marker; accepted/unaccepted 403/409/rejection/malformed receipt stay unknown; stop/reopen")
        let wire=RepairWire();wire.conversationIdentity=a;wire.mode="wait"
        let disk=FeishuOutboxPersistence(load:{ _ in wire.saved },save:{ _,rows in wire.saved=rows })
        let box=FeishuConversationOutbox(persistence:disk);box.bind(a);box.select(c);box.draft="synthetic"
        precondition(box.enqueue());let event=box.records[0].eventId
        let task=Task { await box.flush(using:wire) };await waitFor { wire.held != nil }
        precondition(wire.saved[0].status=="unknown")
        task.cancel();wire.held!.resume(throwing:CancellationError());wire.held=nil;await task.value
        precondition(wire.saved[0].status=="unknown" && box.records[0].status=="unknown")
        let reload=FeishuConversationOutbox(persistence:disk);reload.bind(a)
        precondition(reload.records[0].eventId==event)
        print("PASS: cancelled POST persists uncertainty; restart retains event")
        // Ambiguous disk save (rename happened, fsync failed): no new ID/network until reload.
        var ambiguousRows:[FeishuPendingText]=[];var failing=true;var saves=0
        let uncertain=FeishuConversationOutbox(persistence:FeishuOutboxPersistence(load:{ _ in ambiguousRows },save:{ _,rows in
            saves += 1;ambiguousRows=rows;if failing { throw FeishuSendingFailure.storage }
        }))
        uncertain.bind(a);uncertain.select(c);uncertain.draft="synthetic"
        precondition(!uncertain.enqueue());let ambiguousEvent=ambiguousRows[0].eventId
        precondition(uncertain.storageBlocked && !uncertain.enqueue() && saves==1)
        failing=false;uncertain.reloadStorage()
        precondition(!uncertain.storageBlocked && uncertain.draft.isEmpty && uncertain.records[0].eventId==ambiguousEvent)
        print("PASS: uncertain persistence blocks capture until same-event reconciliation")
        let good=FeishuPendingText(eventId:"fixture",conversationId:"fc_dm",text:"synthetic",status:"pending",accepted:false,messageId:nil,attempted:false)
        var badCases:[[FeishuPendingText]]=[[good,good],Array(repeating:good,count:201)]
        var bad=good;bad.status="forged";badCases.append([bad])
        bad=good;bad.status="sent";badCases.append([bad])
        bad=good;bad.status="pending";bad.messageId="om_one";badCases.append([bad])
        bad=good;bad.attempts=7;badCases.append([bad])
        bad=FeishuPendingText(eventId:"bad/id",conversationId:"fc_dm",text:"synthetic",status:"pending",accepted:false,messageId:nil);badCases.append([bad])
        bad=FeishuPendingText(eventId:"valid",conversationId:"bad",text:"synthetic",status:"pending",accepted:false,messageId:nil);badCases.append([bad])
        for rows in badCases {
            var overwrites=0
            let corrupt=FeishuConversationOutbox(persistence:FeishuOutboxPersistence(load:{ _ in rows },save:{ _,_ in overwrites += 1 }))
            corrupt.bind(a);corrupt.select(c);corrupt.draft="synthetic";precondition(!corrupt.enqueue() && corrupt.storageBlocked && overwrites==0)
        }
        print("PASS: invalid loaded fields/coherence/duplicates/limits cannot overwrite original storage")
        // Six failed receipt queries stop visibly; successful queries do not use this budget.
        let pollingWire=RepairWire();pollingWire.conversationIdentity=a;pollingWire.mode="pending"
        let polling=FeishuConversationOutbox(persistence:FeishuOutboxPersistence(load:{ _ in pollingWire.saved },save:{ _,rows in pollingWire.saved=rows }))
        polling.bind(a);polling.select(c);polling.draft="synthetic";precondition(polling.enqueue())
        let pollingEvent=polling.records[0].eventId
        await polling.flush(using:pollingWire,retryNow:true)
        precondition(polling.records[0].accepted)
        pollingWire.mode="offline"
        for _ in 0..<6 { await polling.flush(using:pollingWire,retryNow:true) }
        precondition(polling.records[0].status=="unknown" && polling.records[0].stopped && polling.hasRetryableOriginals)
        let automaticCalls=pollingWire.calls;await polling.flush(using:pollingWire);precondition(pollingWire.calls==automaticCalls)
        pollingWire.mode="sent";await polling.flush(using:pollingWire,retryNow:true)
        precondition(polling.records[0].eventId==pollingEvent && polling.records[0].status=="sent" && !polling.hasRetryableOriginals)
        // Real corrupt disk is retained unchanged, and never overwritten by enqueue.
        let corruptRoot=URL(fileURLWithPath:CommandLine.arguments[1]).appendingPathComponent("corrupt-"+UUID().uuidString)
        let realDisk=FeishuOutboxPersistence.directory(corruptRoot)
        try realDisk.save(a,[good])
        let diskFile=try FileManager.default.contentsOfDirectory(at:corruptRoot,includingPropertiesForKeys:nil)[0]
        let badBytes=Data("corrupt synthetic fixture".utf8);try badBytes.write(to:diskFile)
        let corruptDisk=FeishuConversationOutbox(persistence:realDisk);corruptDisk.bind(a);corruptDisk.select(c);corruptDisk.draft="synthetic"
        precondition(corruptDisk.storageBlocked && !corruptDisk.enqueue())
        let untouched=try Data(contentsOf:diskFile);precondition(untouched==badBytes)
        print("PASS: retry exhaustion remains unknown with same-event manual reconciliation; real corrupt disk preserved")
        let fullWire=RepairWire();fullWire.conversationIdentity=a;fullWire.mode="sent"
        let full=FeishuConversationOutbox(persistence:FeishuOutboxPersistence(load:{ _ in fullWire.saved },save:{ _,rows in fullWire.saved=rows }))
        full.bind(a);full.select(c)
        for _ in 0..<200 { full.draft=String(repeating:"s",count:20_000);precondition(full.enqueue()) }
        full.draft="capacity";precondition(full.capacityFull && !full.enqueue())
        let frozen=full.records.map(\.eventId)
        await full.flush(using:fullWire)
        precondition(full.records.filter(\.confirmedTerminal).count==20)
        precondition(full.enqueue() && full.records.count<=200)
        precondition(Array(full.records.dropLast().filter { !$0.confirmedTerminal }.map(\.eventId))==Array(frozen.suffix(180)))
        print("PASS: full capacity still flushes originals; only confirmed terminal cache compacts; receipt growth fits")
        // Old flush completion must not clear ownership of a new generation's flush.
        var accountRows:[FeishuConversationIdentity:[FeishuPendingText]]=[:]
        let firstWire=RepairWire();firstWire.conversationIdentity=a;firstWire.mode="wait"
        let secondWire=RepairWire();secondWire.conversationIdentity=b;secondWire.mode="wait"
        let rotating=FeishuConversationOutbox(persistence:FeishuOutboxPersistence(load:{accountRows[$0] ?? []},save:{identity,rows in
            accountRows[identity]=rows;if identity==a {firstWire.saved=rows}else{secondWire.saved=rows}
        }))
        rotating.bind(a);rotating.select(c);rotating.draft="first";precondition(rotating.enqueue())
        let older=Task {await rotating.flush(using:firstWire)};await waitFor {firstWire.held != nil}
        rotating.bind(b);rotating.select(c);rotating.draft="second";precondition(rotating.enqueue())
        let newer=Task {await rotating.flush(using:secondWire)};await waitFor {secondWire.held != nil}
        firstWire.held!.resume(throwing:CancellationError());await older.value
        precondition(rotating.flushing && rotating.identity==b && rotating.records[0].text=="second")
        secondWire.held!.resume(throwing:CancellationError());await newer.value
        precondition(!rotating.flushing && accountRows[a]![0].status=="unknown")
        print("PASS: account rotation isolates durable markers and old task cannot clear newer ownership")
    }
}
#endif
