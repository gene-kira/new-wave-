# Auto-generated strategies module
weights = {"novelty_weight": 0.25, "utility_weight": 0.35, "impact_weight": 0.25, "curiosity_weight": 0.15}
def adjust_weights(perf):
    w = dict(weights)
    cr = perf.get("completion_rate", 0.0)
    avg_obj = perf.get("avg_objective", 0.0)
    feed_entropy = perf.get("feed_entropy", 0.0)
    sys_entropy = perf.get("sys_entropy", 0.0)
    if cr < 0.05:
        w["novelty_weight"] = min(1.2, w["novelty_weight"] + 0.05)
        w["curiosity_weight"] = min(1.2, w["curiosity_weight"] + 0.03)
    if avg_obj > 1.0:
        w["utility_weight"] = min(1.2, w["utility_weight"] + 0.04)
        w["impact_weight"] = min(1.2, w["impact_weight"] + 0.02)
    w["novelty_weight"] = min(1.3, w["novelty_weight"] + 0.01 * feed_entropy)
    w["curiosity_weight"] = min(1.3, w["curiosity_weight"] + 0.01 * sys_entropy)
    return w
