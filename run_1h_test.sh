#!/bin/bash
# 1-Stunden Live-Test mit Auto-Restart
LOG=/tmp/bot_1h.txt
SUMMARY=/tmp/bot_1h_summary.txt
END=$(($(date +%s) + 3600))

echo "=== 1h Live-Test gestartet: $(date) ===" | tee $LOG
echo "Ende: $(date -d @$END)" | tee -a $LOG
echo "" | tee -a $LOG

kill $(ps aux | grep mm_main | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 2

run=0
while [ $(date +%s) -lt $END ]; do
    run=$((run+1))
    echo "--- Bot-Run #$run gestartet: $(date) ---" | tee -a $LOG
    timeout $((END - $(date +%s))) python -m polymarket_btc_bot.mm_main --mode paper 2>&1 | \
        grep -v "Traceback\|File \"/usr/\|ValueError: unsupported\|Call stack:\|run_until_complete\|run_forever\|_run_once\|_context.run\|handle._run\|self._fill_monitor\|self._check_fills\|self._paper_fill_check\|self.market_maker.process_fill\|self.inv_mgr.record_fill\|logger.info\|msg = msg\|~~~~\|^^^^" | \
        tee -a $LOG
    echo "--- Bot-Run #$run beendet: $(date) ---" | tee -a $LOG
    [ $(date +%s) -lt $END ] && sleep 2
done

echo "" | tee -a $LOG
echo "=== 1h Test abgeschlossen: $(date) ===" | tee -a $LOG

# Zusammenfassung extrahieren
echo "" > $SUMMARY
echo "========================================" >> $SUMMARY
echo "  ERGEBNISSE 1h LIVE-TEST (Paper Mode)" >> $SUMMARY
echo "========================================" >> $SUMMARY
grep "Markt aufgelöst\|GEWONNEN\|VERLOREN\|PnL" $LOG >> $SUMMARY
echo "" >> $SUMMARY
echo "Alle Markt-Auflösungen:" >> $SUMMARY
grep "Markt abgelaufen:.*Ergebnis\|Markt aufgelöst" $LOG >> $SUMMARY
