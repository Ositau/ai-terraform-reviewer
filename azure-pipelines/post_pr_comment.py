"""Post (or update) the review as a comment thread on an Azure DevOps pull request.

Uses the pipeline's own token (System.AccessToken), so no personal access token is needed.
The thread starts with a hidden marker, so re-runs update the same thread instead of adding new ones.
Works for Azure Repos pull requests. For GitHub repositories built in Azure DevOps, use the
GitHub Action instead, or post with the GitHub API.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

MARKER = "<!-- ai-terraform-reviewer -->"


def call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"{}")


def main(path: str) -> int:
    if os.getenv("BUILD_REPOSITORY_PROVIDER", "TfsGit") != "TfsGit":
        print("Not an Azure Repos pull request; skipping the comment (the review is in the run summary).")
        return 0
    token = os.environ["SYSTEM_ACCESSTOKEN"]
    base = (f"{os.environ['SYSTEM_COLLECTIONURI']}{os.environ['SYSTEM_TEAMPROJECTID']}/_apis/git/repositories/"
            f"{os.environ['BUILD_REPOSITORY_ID']}/pullRequests/{os.environ['SYSTEM_PULLREQUEST_PULLREQUESTID']}/threads")
    body = open(path, encoding="utf-8").read()
    threads = call("GET", f"{base}?api-version=7.1", token).get("value", [])
    for t in threads:
        first = (t.get("comments") or [{}])[0]
        if str(first.get("content", "")).startswith(MARKER):
            call("PATCH", f"{base}/{t['id']}/comments/{first['id']}?api-version=7.1", token, {"content": body})
            print(f"Updated review comment in thread {t['id']}.")
            return 0
    t = call("POST", f"{base}?api-version=7.1", token, {"comments": [{"parentCommentId": 0, "content": body, "commentType": 1}], "status": 1})
    print(f"Posted review comment in thread {t.get('id')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
