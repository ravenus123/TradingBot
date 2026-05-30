#!/usr/bin/env python3
"""
PHASE 1: ENGINE VERIFICATION & OUT-OF-SAMPLE TEST
Proves system is honest for $10k deployment
"""
# --- path bootstrap (allow running as a script: add BOT/ to sys.path) ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

print("="*80)
print("PHASE 1 & 2: ENGINE VERIFICATION + OUT-OF-SAMPLE TEST")
print("="*80)
print(f"Date: {datetime.now(timezone.utc).isoformat()}")
print()

# ============================================================================
# PHASE 1: ENGINE VERIFICATION
# ============================================================================

print("\n" + "="*80)
print("PHASE 1: ENGINE VERIFICATION")
print("="*80)

print("""
Goal: Verify backtest_improved.py calculations are correct

Method:
  1. Run backtest on small sample
  2. Pick 5 trades
  3. Calculate manually
  4. Compare results
  5. If match exactly → engine is honest ✅
  6. If drift > 1% → investigate ❌

Why this matters:
  If engine is off by 20%, results are meaningless
  $10k deployment requires verified engine
""")

print("\n✅ TO DO:")
print("  [ ] Run: python backtest_improved.py --verify --output=trades.json")
print("  [ ] Output will include detailed trade information")
print("  [ ] Manually verify 5 trades from the output")
print("  [ ] Compare to expected calculations")
print("  [ ] Report findings in VERIFICATION_REPORT.md")

# ============================================================================
# PHASE 2: OUT-OF-SAMPLE TEST
# ============================================================================

print("\n" + "="*80)
print("PHASE 2: OUT-OF-SAMPLE TEST FRAMEWORK")
print("="*80)

print("""
Goal: Prove system generalizes (not just lucky on one chunk of data)

Method:
  1. Take all 30,000 bars (or available)
  2. Split into:
     - 60% (18,000 bars): IN-SAMPLE (used for developing strategy)
     - 20% (6,000 bars): VALIDATION (test on "new" data)
     - 20% (6,000 bars): UNTOUCHED (final holdout test)
  3. Run backtest on all three
  4. Compare results
  
Success = all three chunks show similar results (±10%)
Failure = untouched chunk performs 50% worse (means overfit)

Why this matters:
  If only works on backtest period → overfit
  If works on all periods → real edge
  $10k requires proof of generalization
""")

print("\n✅ OUT-OF-SAMPLE TEST STRUCTURE:")

test_plan = {
    "EURUSD": {
        "total_bars": 30000,
        "in_sample": (0, 18000),
        "validation": (18000, 24000),
        "untouched": (24000, 30000),
    },
    "NAS100": {
        "total_bars": 30000,
        "in_sample": (0, 18000),
        "validation": (18000, 24000),
        "untouched": (24000, 30000),
    },
    "XAUUSD": {
        "total_bars": 30000,
        "in_sample": (0, 18000),
        "validation": (18000, 24000),
        "untouched": (24000, 30000),
    }
}

for symbol, plan in test_plan.items():
    print(f"\n{symbol}:")
    print(f"  Total bars: {plan['total_bars']}")
    print(f"  In-sample (60%): bars {plan['in_sample'][0]}-{plan['in_sample'][1]}")
    print(f"  Validation (20%): bars {plan['validation'][0]}-{plan['validation'][1]}")
    print(f"  Untouched (20%): bars {plan['untouched'][0]}-{plan['untouched'][1]}")

# ============================================================================
# EXPECTED RESULTS
# ============================================================================

print("\n" + "="*80)
print("EXPECTED RESULTS FOR $10K CONFIDENCE")
print("="*80)

expected = {
    "EURUSD": {
        "in_sample_profitable": "90%+",
        "validation_profitable": "85-95%",  # ±5% ok
        "untouched_profitable": "85-95%",   # This matters most
        "backtest_win_rate": "76.7%",
        "expected_win_rate": "70-83%",
    },
    "NAS100": {
        "in_sample_profitable": "80%+",
        "validation_profitable": "75-85%",
        "untouched_profitable": "75-85%",
        "backtest_win_rate": "62.2%",
        "expected_win_rate": "57-67%",
    },
    "XAUUSD": {
        "in_sample_profitable": "90%+",
        "validation_profitable": "85-95%",
        "untouched_profitable": "85-95%",
        "backtest_win_rate": "52.2%",
        "expected_win_rate": "47-57%",
    }
}

print("\nFor $10K deployment, expect:")
for symbol, metrics in expected.items():
    print(f"\n{symbol}:")
    for key, val in metrics.items():
        print(f"  {key}: {val}")

# ============================================================================
# RED FLAGS
# ============================================================================

print("\n" + "="*80)
print("RED FLAGS THAT BLOCK $10K DEPLOYMENT")
print("="*80)

red_flags = [
    "❌ Engine verification shows drift >5%",
    "❌ Untouched test shows <70% profitable (means overfit)",
    "❌ Untouched test is 50%+ worse than in-sample",
    "❌ Win rate drops >15% on untouched data",
    "❌ Any calculation mismatch >1%",
]

for flag in red_flags:
    print(flag)

print("\nIf ANY red flag appears: FIX IT before $10k deployment")

# ============================================================================
# NEXT STEPS
# ============================================================================

print("\n" + "="*80)
print("IMMEDIATE NEXT STEPS")
print("="*80)

print("""
1. VERIFY BACKTEST ENGINE (1 hour)
   - Get detailed trade output from backtest
   - Pick 5 random trades
   - Calculate entry/exit manually
   - Compare results
   - Report in VERIFICATION_REPORT.md

2. RUN OUT-OF-SAMPLE TEST (2 hours)
   - Implement 60/20/20 split in backtest
   - Run on all three data chunks
   - Collect results
   - Report in OUT_OF_SAMPLE_RESULTS.json

3. DECISION
   - If both pass → Phase 2: Safety Systems
   - If either fails → Debug, fix, restart

After these complete, you have:
✅ Proof engine is accurate
✅ Proof system generalizes (not overfit)
✅ High confidence for $10k deployment
""")

# ============================================================================
# CREATE VERIFICATION TEMPLATE
# ============================================================================

verification_template = {
    "phase": 1,
    "date": datetime.now(timezone.utc).isoformat(),
    "engine_verification": {
        "status": "PENDING",
        "trades_checked": 0,
        "matches": 0,
        "mismatches": 0,
        "max_drift": 0,
        "result": "UNKNOWN"
    },
    "out_of_sample_test": {
        "status": "PENDING",
        "eurusd": {
            "in_sample_profitable": 0,
            "validation_profitable": 0,
            "untouched_profitable": 0,
            "result": "UNKNOWN"
        },
        "nas100": {
            "in_sample_profitable": 0,
            "validation_profitable": 0,
            "untouched_profitable": 0,
            "result": "UNKNOWN"
        },
        "xauusd": {
            "in_sample_profitable": 0,
            "validation_profitable": 0,
            "untouched_profitable": 0,
            "result": "UNKNOWN"
        }
    },
    "recommendation": "PENDING"
}

# Save template
template_path = Path(__file__).parent / 'VERIFICATION_RESULTS_TEMPLATE.json'
with open(template_path, 'w') as f:
    json.dump(verification_template, f, indent=2)

print(f"\n✅ Template created: {template_path}")

# ============================================================================
# SUMMARY
# ============================================================================

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

print("""
Current Status: ⏳ NOT VERIFIED YET

To be $10k deployment ready, you need:
  ✅ Engine verified (no calculation errors)
  ✅ Out-of-sample passed (system generalizes)
  ✅ Safety systems built (12 hours work)
  ✅ Demo test completed (7 days)
  ✅ Monitoring verified (alerts working)
  ✅ Emergency stops tested (capital protected)

Time estimate to $10k ready: ~30 hours of work + 8 days waiting for tests

Expected outcome: System you can confidently deploy $10k to
""")

print("="*80)
print("Ready to proceed with Phase 1 verification?")
print("="*80)
