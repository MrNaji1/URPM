import sys

if "--check-updates" in sys.argv[1:]:
    # The background check: no window, so don't load GTK at all.
    from urpm.notify import main as check_main
    sys.exit(check_main())

from urpm.ui.app import main  # noqa: E402

sys.exit(main(sys.argv))
