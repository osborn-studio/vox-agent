import XCTest
@testable import VoxLog

final class ModelListFetcherTests: XCTestCase {

    func testBailianCNModelParsingKeepsEveryAccessibleModel() throws {
        let payload = """
        {
          "object": "list",
          "data": [
            { "id": "qwen3.5-flash" },
            { "id": "qwen-plus" },
            { "id": "text-embedding-v4" },
            { "id": "wanx2.1-t2i-turbo" }
          ]
        }
        """
        let data = try XCTUnwrap(payload.data(using: .utf8))

        let models = ModelListFetcher.parseModelsResponse(data: data, provider: .bailian)

        XCTAssertEqual(models, [
            "qwen-plus",
            "qwen3.5-flash",
            "text-embedding-v4",
            "wanx2.1-t2i-turbo",
        ])
    }
}
