# at top:
from app.policy import apply_policy, apply_policy_with_stats

# inside your /api/news handler, AFTER you’ve built `items` and BEFORE you paginate:
debug = (request.args.get("debug") == "1") if hasattr(request, "args") else (query_params.get("debug") == "1")

team_code = (team or "ARS")
if debug:
    items, stats = apply_policy_with_stats(items, team_code=team_code)
else:
    items = apply_policy(items, team_code=team_code)

# ... then paginate as you already do:
# start = (page-1)*pageSize; end = start+pageSize; sliced = items[start:end]

# ... and when building the response dict:
resp = {
    "items": sliced,
    "page": page,
    "pageSize": pageSize,
    "total": len(items),
}
if debug:
    resp["policyDebug"] = stats
return jsonify(resp)


