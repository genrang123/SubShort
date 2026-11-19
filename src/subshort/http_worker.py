"""Isolated HTTP worker: JSON request on stdin, JSON result on stdout.

Runs in a child process so a cancelled pipeline can kill an in-flight request.
The request carries the API key on stdin, never in argv, and the answer carries
status/body plus a Retry-After value only, so the key never reaches logs or
process listings and no response headers leak out.
"""
import json
import sys
import urllib.error
import urllib.request


def main():
    # A frozen windowed build has no sys.stdin/sys.stdout; the parent's pipes are still fd 0/1.
    stdin = sys.stdin or open(0, encoding="utf-8", closefd=False)
    stdout = sys.stdout or open(1, "w", encoding="utf-8", closefd=False)
    request = json.load(stdin)
    try:
        req = urllib.request.Request(request["url"], request["payload"].encode(), request["headers"])
        with urllib.request.urlopen(req, timeout=request["timeout"]) as response:
            answer = {"ok": True, "body": response.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        answer = {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8", "replace")}
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after is not None:
            answer["retry_after"] = retry_after
    except urllib.error.URLError as exc:
        # Only the failure kind leaves this process; the reason may name the URL.
        answer = {"ok": False, "error": "timeout" if isinstance(exc.reason, TimeoutError) else "network"}
    except TimeoutError:
        answer = {"ok": False, "error": "timeout"}
    except Exception:
        # Never echo the exception: it can carry the URL, headers or response text.
        answer = {"ok": False, "error": "network"}
    stdout.write(json.dumps(answer))
    stdout.flush()


if __name__ == "__main__":
    main()
