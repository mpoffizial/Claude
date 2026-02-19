"""Entry point for: python -m polymarket_btc_bot.chainlink_lag"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "analyze":
    # Remove "analyze" from argv so argparse works
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    from polymarket_btc_bot.chainlink_lag.lag_analyzer import main
    main()
else:
    import asyncio
    from polymarket_btc_bot.chainlink_lag.lag_logger import main
    asyncio.run(main())
