"""Compile production resolver and inspect synthetic Git/worktree fixtures only."""
import argparse
import subprocess
import tempfile
from pathlib import Path

HARNESS = r'''
import Foundation
@main struct Check {
    static func main() async throws {
        precondition(ContextRepositoryResolver.repositorySlug("https://user:secret@example.com/team/vox.git") == "team/vox")
        precondition(ContextRepositoryResolver.repositorySlug("git@example.com:team/vox.git") == "team/vox")
        precondition(ContextRepositoryResolver.repositorySlug("ssh://git@example.com/team/vox") == "team/vox")
        for remote in ["/Users/private/vox", "file:///Users/private/vox", "ext::sh evil", "https://example.com/team/vox?token=secret", "https://example.com/team/%76ox", "https://example.com/team/vox.git\n", "git@host:/team/vox", "git@host:team/nested/vox", "https://example.com/../vox"] {
            precondition(ContextRepositoryResolver.repositorySlug(remote) == nil)
        }
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let original = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("repo/subdirectory"))
        precondition(original.repositories == ["team/other", "team/vox"])
        let linked = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("linked"))
        precondition(linked.repositories == original.repositories && linked.branch == "feature")
        let detached = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("detached"))
        precondition(detached.branch == nil && detached.repositories == original.repositories)
        let submodule = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("repo/module"))
        precondition(submodule.repositories.isEmpty, "local file submodule remote must remain unknown")
        do { _ = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("not-git")); fatalError("non-repo accepted") } catch {}
        do { _ = try await ContextRepositoryResolver.inspect(directory: root.appendingPathComponent("missing")); fatalError("missing directory accepted") } catch {}
        do { _ = try ContextRepositoryResolver.parseRemotes(Data(repeating: 65, count: 65537)); fatalError("unbounded parser") } catch {}
        print("PASS: safe credential stripping, unsupported URL/path rejection, nested monorepo, renamed/multiple remotes, linked worktree, missing directory, bounded parsing")
    }
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="vox-context-git-") as folder:
        fixture = Path(folder)
        repo = fixture / "repo"
        repo.mkdir()
        (repo / "subdirectory").mkdir()
        def git(*arguments):
            subprocess.run(["/usr/bin/git", "-C", str(repo), *arguments], check=True, capture_output=True)
        git("init")
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-m", "fixture")
        git("remote", "add", "renamed", "https://user:synthetic-secret@example.com/team/vox.git")
        git("remote", "add", "second", "git@example.com:team/other.git")
        git("worktree", "add", "-b", "feature", str(fixture / "linked"))
        git("worktree", "add", "--detach", str(fixture / "detached"))
        (fixture / "not-git").mkdir()
        child = fixture / "child"
        child.mkdir()
        subprocess.run(["/usr/bin/git", "-C", str(child), "init"], check=True, capture_output=True)
        subprocess.run(["/usr/bin/git", "-C", str(child), "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
        git("-c", "protocol.file.allow=always", "submodule", "add", str(child), "module")
        source = fixture / "Check.swift"
        source.write_text(HARNESS)
        executable = fixture / "check"
        subprocess.run(["swiftc", "-sdk", args.sdk, "-parse-as-library", str(root / "macos/VoxLog/Services/ContextRepositoryResolver.swift"), str(source), "-o", str(executable)], check=True)
        subprocess.run([str(executable), str(fixture)], check=True, timeout=15)


if __name__ == "__main__":
    main()
