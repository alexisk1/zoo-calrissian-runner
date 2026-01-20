# diagnostics.py

import json
from typing import Dict, Any, Optional
from loguru import logger


# ---------- canonical kill_cause helpers ----------

def _mk_kill_cause(
    *,
    step: str = "unknown",
    exit_code: int = -1,
    error_msg: str = "Unknown termination cause.",
) -> Dict[str, Any]:
    """
    Create a normalized kill_cause object.

    Returns:
        {"step": <str>, "exit_code": <int>, "error_msg": <str>}
    """
    return {
        "step": step,
        "exit_code": int(exit_code) if isinstance(exit_code, (int, float)) else -1,
        "error_msg": str(error_msg) if error_msg is not None else "Unknown termination cause.",
    }


# ---------- child-level checks ----------

def _child_is_oom(c: Dict[str, Any]) -> bool:
    ec = c.get("exit_code")
    if ec == 137:
        logger.debug(f"Child {c.get('name')} exit 137 - OOM")
        return True
    if c.get("signal") == 9:
        logger.debug(f"Child {c.get('name')} signal 9 - OOM")
        return True
    return False


def _child_zero_runtime(c: Dict[str, Any]) -> bool:
    try:
        el = int(c.get("elapsed_seconds") or 0.0)
    except Exception:
        el = 0.0
    cpus = float(c.get("cpus") or 0.0)
    ram  = float(c.get("ram_megabytes") or 0.0)
    zr = (el == 0.0) and (cpus > 0.0 or ram > 0.0)
    if zr:
        logger.debug(f"Child {c.get('name')} has zero runtime with resources - suspect pre-exec")
    return zr

def _child_exit_code_issue(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Detects a non-zero exit code in a child and returns its detailed message (if available).
    If the child includes a nested 'message' dict, unwrap it into a normalized kill_cause.
    """
    print(str(c))
    ec = c.get("exit_code")
    if ec is None or ec == 0:
        return None

    # Nested structured message present?
    msg = c.get("message")
    if isinstance(msg, dict):
        return msg
    
        
    # Fallback: no embedded message, just use exit code + step name
    step = c.get("name") or "unknown"
    logger.info(f"Child {step} non-zero exit {ec} {msg}")
    return _mk_kill_cause(
        step=step,
        exit_code=ec,
        error_msg=msg
    )

# ---------- report.json classification -> kill_cause dict ----------

def classify_from_report(usage_report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Return a normalized kill_cause dict using report.json alone.
    The goal is to provide the best possible 'error_msg' text; we do not attach a 'reason' field.
    """
    logger.info("Classifying outcome from report.json only")

    if not usage_report:
        logger.warning("No report.json available")
        return _mk_kill_cause(
            error_msg="No usage report available."
        )

    children = usage_report.get("children") or []
    total_elapsed = usage_report.get("elapsed_seconds")

    # No steps and no elapsed: pre-exec failure likely
    if (not children) and (total_elapsed in (0, 0.0, None)):
        logger.info("Report shows no steps ran at all")
        return _mk_kill_cause(
            error_msg="No steps ran; likely a pre-execution failure."
        )

    # All children succeeded
    if children and all((c.get("exit_code") == 0) for c in children):
        steps = ", ".join(c.get("name","?") for c in children)
        logger.info("All children succeeded")
        return _mk_kill_cause(
            step=children[-1].get("name", "unknown"),
            exit_code=0,
            error_msg=f"All steps succeeded: {steps}."
        )

    # OOM has priority among failures
    ooms = [c for c in children if _child_is_oom(c)]
    if ooms:
        names = ", ".join(f"{c.get('name','?')} (exit 137)" for c in ooms)
        logger.info(f"Detected OOM in: {names}")
        return _mk_kill_cause(
            step=ooms[0].get("name", "unknown"),
            exit_code=137,
            error_msg=f"Out-of-memory detected in: {names}."
        )

    for c in children:
        exit_issue = _child_exit_code_issue(c)
        logger.info(f"Checking child report: {c}")
        if exit_issue:
            return exit_issue

    return _mk_kill_cause(
        step="unknown",
        exit_code=-1,
        error_msg=f"Unkown failure in children report.",
    )