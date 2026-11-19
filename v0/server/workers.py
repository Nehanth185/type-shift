"""Manage per-worker access tokens (used when the server runs with AUTH_MODE=worker).

  python workers.py add w1 [--workspace demo]   create w1 (or rotate its token); prints the token ONCE
  python workers.py list                        worker ids + workspace (tokens are not stored, only hashes)
  python workers.py revoke w1                   delete w1's token; its requests get 401 from then on

Uses the same database as the server (GLASSES_DB, default data/glasses.db). Local admin tool only:
there is deliberately no HTTP endpoint that mints tokens.
"""
import argparse, os, sys

from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("worker_id"); a.add_argument("--workspace", default=None,
                                                                         help="lock this worker to one workspace")
    sub.add_parser("list")
    r = sub.add_parser("revoke"); r.add_argument("worker_id")
    args = ap.parse_args(argv)
    st = Store(os.environ.get("GLASSES_DB", os.path.join(HERE, "data", "glasses.db")))
    if args.cmd == "add":
        tok = st.add_worker(args.worker_id.strip(), args.workspace)
        print(tok)
        print(f"# token for worker '{args.worker_id}'" + (f" (workspace {args.workspace})" if args.workspace else "")
              + ". Shown once; put it in the companion app's Settings > Worker token.", file=sys.stderr)
    elif args.cmd == "list":
        for w in st.list_workers():
            print(f"{w['worker_id']}\tworkspace={w['workspace_id'] or '*'}\tcreated={w['created_at']}")
    elif args.cmd == "revoke":
        n = st.revoke_worker(args.worker_id)
        print("revoked" if n else "no such worker")
        return 0 if n else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
