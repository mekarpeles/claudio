"""claudio — native Claude Code inter-session messaging CLI.

Thin wrapper over claudio.native (the wire protocol) and claudio.registry (the
logical-name -> sessionId alias map). No daemon, no tmux, no socket server:
Claude Code owns the inbox now, so claudio only resolves targets and delivers.

Commands:
    claudio ls                         List live Claude sessions.
    claudio whoami                     Show the current session (from env).
    claudio register <name> [--session <id>]
                                       Bind a logical name to a session
                                       (defaults to the current one). This is
                                       the "save socket on startup" step.
    claudio unregister <name>          Remove an alias.
    claudio resolve <name|id|pid>      Show the session a selector resolves to.
    claudio aliases                    List all logical-name -> sessionId binds.
    claudio send <name|id|pid> <msg>   Deliver a message natively.

Sender identity for `send` comes from --from, else $CLAUDIO_AGENT_NAME, else the
current session's name.
"""

import argparse
import os
import sys

from claudio import native, registry

CURRENT_SESSION_ID = os.environ.get("CLAUDE_CODE_SESSION_ID")


def _resolve_selector(selector):
    """Map a logical alias to a sessionId first, then hand to native.resolve."""
    sid = registry.session_id_for(selector)
    return native.resolve(sid or selector)


def _short(sid, n=8):
    return (sid or "")[:n]


def cmd_ls(args):
    sessions = native.list_sessions(include_dead=args.all)
    if not sessions:
        print("claudio: no live Claude sessions found")
        return 0
    aliases = {v["sessionId"]: k for k, v in registry.all_aliases().items()
               if v.get("sessionId")}
    print(f"{'NAME':<16} {'ALIAS':<12} {'PID':>7}  {'STATUS':<6} {'SESSION':<10} CWD")
    for r in sorted(sessions, key=lambda x: x.get("name") or ""):
        print(f"{(r.get('name') or '?'):<16} "
              f"{aliases.get(r.get('sessionId'), ''):<12} "
              f"{r.get('pid', ''):>7}  "
              f"{(r.get('status') or '?'):<6} "
              f"{_short(r.get('sessionId')):<10} "
              f"{r.get('cwd', '')}")
    return 0


def cmd_whoami(args):
    if not CURRENT_SESSION_ID:
        print("claudio: not running inside a Claude Code session "
              "(CLAUDE_CODE_SESSION_ID unset)", file=sys.stderr)
        return 1
    try:
        rec = native.resolve(CURRENT_SESSION_ID)
        print(f"name={rec.get('name')} pid={rec.get('pid')} "
              f"session={CURRENT_SESSION_ID} socket={rec.get('messagingSocketPath')}")
    except native.SessionNotFound:
        print(f"session={CURRENT_SESSION_ID} (no published record yet)")
    return 0


def cmd_register(args):
    info = registry.register(args.name, session_id=args.session, socket=args.socket)
    if not info.get("sessionId") and not info.get("socket"):
        print("claudio: no session to register (run inside a Claude session, or "
              "pass --session / --socket)", file=sys.stderr)
        registry.unregister(args.name)
        return 1
    print(f"claudio: registered '{args.name}' -> session={info.get('sessionId')} "
          f"socket={info.get('socket')}")
    return 0


def cmd_unregister(args):
    removed = registry.unregister(args.name)
    if removed is None:
        print(f"claudio: no alias '{args.name}'", file=sys.stderr)
        return 1
    print(f"claudio: unregistered '{args.name}'")
    return 0


def cmd_resolve(args):
    try:
        rec = _resolve_selector(args.selector)
    except native.SessionNotFound as e:
        print(f"claudio: {e}", file=sys.stderr)
        return 1
    print(f"name={rec.get('name')} pid={rec.get('pid')} "
          f"session={rec.get('sessionId')} status={rec.get('status')} "
          f"socket={rec.get('messagingSocketPath')} cwd={rec.get('cwd')}")
    return 0


def cmd_aliases(args):
    data = registry.all_aliases()
    if not data:
        print("claudio: no aliases registered")
        return 0
    for name, info in sorted(data.items()):
        print(f"{name:<16} -> session={info.get('sessionId')} "
              f"socket={info.get('socket')}")
    return 0


def _sender(explicit):
    """Resolve 'from': --from, else $CLAUDIO_AGENT_NAME, else OS login name.
    Avoids opaque defaults (a raw sessionId) that confuse the receiver.
    """
    import getpass
    if explicit:
        return explicit
    if os.environ.get("CLAUDIO_AGENT_NAME"):
        return os.environ["CLAUDIO_AGENT_NAME"]
    try:
        return getpass.getuser()
    except Exception:
        return "claudio"


def cmd_send(args):
    sender = _sender(args.sender)
    message = " ".join(args.message)
    entry = registry.get(args.selector)
    try:
        # Prefer the stable sessionId (re-resolves the live socket); then the
        # remembered socket; finally treat the selector as a native selector.
        if entry and entry.get("sessionId"):
            try:
                rec = native.send(entry["sessionId"], message, sender=sender,
                                  priority=args.priority,
                                  from_session=CURRENT_SESSION_ID)
                print(f"claudio: delivered to '{args.selector}' "
                      f"(name={rec.get('name')} pid={rec.get('pid')})")
                return 0
            except native.SessionNotFound:
                pass  # session gone; try the remembered socket
        if entry and entry.get("socket"):
            native.send_to_socket(entry["socket"], message, sender=sender,
                                  priority=args.priority,
                                  from_session=CURRENT_SESSION_ID)
            print(f"claudio: forwarded to '{args.selector}' "
                  f"(socket={entry['socket']})")
            return 0
        rec = native.send(args.selector, message, sender=sender,
                          priority=args.priority, from_session=CURRENT_SESSION_ID)
        print(f"claudio: delivered to '{args.selector}' "
              f"(name={rec.get('name')} pid={rec.get('pid')})")
        return 0
    except (native.SessionNotFound, native.DeliveryError) as e:
        print(f"claudio: {e}", file=sys.stderr)
        return 1


def cmd_nudge(args):
    """Manage an agent's self-directed prompt list (the nudges primitive).

    The list is retained from v1; v2 has no delivery loop to auto-fire it, so this
    only edits/lists the list (a native idle-firing home is a follow-up).
    """
    from claudio.nudges import Nudges, nudges_path
    name = (os.environ.get("CLAUDIO_AGENT_NAME")
            or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if not name:
        print("claudio: set CLAUDIO_AGENT_NAME to manage nudges", file=sys.stderr)
        return 1
    nudges = Nudges(nudges_path(name, registry.STATE_DIR))
    if args.action in (None, "list"):
        prompts = nudges.all()
        if not prompts:
            print("(no nudges)")
        for i, p in enumerate(prompts):
            print(f"{i}\t{p}")
        return 0
    if args.action == "add":
        prompt = " ".join(args.rest).strip()
        if not prompt:
            print("claudio: nudge prompt cannot be empty", file=sys.stderr)
            return 1
        print(f"claudio: {'registered' if nudges.add(prompt) else 'already present'}: {prompt}")
        return 0
    if args.action == "remove":
        target = " ".join(args.rest).strip()
        removed = nudges.remove_target(target)  # exact text first, then index
        print(f"claudio: {'removed: ' + removed if removed else 'no such nudge: ' + target}")
        return 0 if removed else 1
    print(f"claudio: unknown nudge action {args.action!r}", file=sys.stderr)
    return 1


def cmd_help(args):
    build_parser().print_help()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="claudio", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("ls", aliases=["list"], help="list live Claude sessions")
    s.add_argument("--all", action="store_true", help="include dead sessions")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("whoami", help="show the current session")
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser("register", help="remember a session's socket under a name")
    s.add_argument("name")
    s.add_argument("--session", help="sessionId (default: current session)")
    s.add_argument("--socket", help="messaging socket path (default: current session)")
    s.set_defaults(func=cmd_register)

    s = sub.add_parser("unregister", help="remove an alias")
    s.add_argument("name")
    s.set_defaults(func=cmd_unregister)

    s = sub.add_parser("resolve", help="show what a selector resolves to")
    s.add_argument("selector")
    s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("aliases", help="list all aliases")
    s.set_defaults(func=cmd_aliases)

    s = sub.add_parser("send", help="deliver a message natively")
    s.add_argument("selector", help="logical name, sessionId, derived name, or pid")
    s.add_argument("message", nargs="+")
    s.add_argument("--from", dest="sender", help="sender name")
    s.add_argument("--priority", default="next", choices=["next", "now"])
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("nudge", help="manage an agent's self-prompt list")
    s.add_argument("action", nargs="?", choices=["list", "add", "remove"],
                   default="list")
    s.add_argument("rest", nargs="*")
    s.set_defaults(func=cmd_nudge)

    s = sub.add_parser("help", help="show this help")
    s.set_defaults(func=cmd_help)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
