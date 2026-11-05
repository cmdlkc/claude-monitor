"""Entry point for the packaged Claude Monitor.exe.

  Claude Monitor.exe               install on first run, otherwise open the overlay
  Claude Monitor.exe overlay       open the overlay
  Claude Monitor.exe hook          Claude Code hook (payload on stdin)
  Claude Monitor.exe statusline    Claude Code status line (payload on stdin)
  Claude Monitor.exe focus <pid>   bring that Claude session's terminal to the front
  Claude Monitor.exe install | uninstall
"""
import sys


def main():
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "hook":
        import hook
        hook.run()
    elif cmd == "statusline":
        import statusline
        statusline.run()
    elif cmd == "focus":
        import focus
        focus.run(args)
    elif cmd == "install":
        import install
        install.install()
    elif cmd == "uninstall":
        import install
        install.uninstall()
    else:
        import install
        if cmd != "overlay" and not install.is_installed_here():
            install.install()
            return
        import overlay
        overlay.run()


if __name__ == "__main__":
    main()
