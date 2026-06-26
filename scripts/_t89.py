import sys
sys.dont_write_bytecode = True
import breakability_analyst as ba

uuid_pr = {
    "number": 67, "pr_num": 67, "package": "uuid",
    "files_importing": [],
    "deterministic": {"usages": [{"file": "x.ts", "line": 5, "symbol": "v4"}] * 19},
}
jwks_pr = {
    "number": 66, "pr_num": 66, "package": "jwks-rsa",
    "files_importing": [],
    "deterministic": {"usages": []},
}
reached_pr = {
    "number": 208, "pr_num": 208, "package": "left-pad",
    "files_importing": ["src/a.ts", "src/b.ts"],
    "deterministic": {"usages": [{"file": "src/a.ts", "line": 3, "symbol": "leftPad"}]},
}

for name, pr in [("uuid#67", uuid_pr), ("jwks#66", jwks_pr), ("reached#208", reached_pr)]:
    r = ba._normalize_reachability(pr)
    print(name, "-> reached=%s usages_len=%d files_len=%d" % (r["reached"], len(r["usages"]), len(r["import_files"])))

print("REC uuid#67  :", ba._get_recommendation(uuid_pr))
print("REC jwks#66  :", ba._get_recommendation(jwks_pr))
print("REC reached  :", ba._get_recommendation(reached_pr))

assert ba._normalize_reachability(uuid_pr)["reached"] is False
assert ba._normalize_reachability(jwks_pr)["reached"] is False
assert ba._normalize_reachability(reached_pr)["reached"] is True
assert "changelog for any notable changes" in ba._get_recommendation(uuid_pr)
assert "changelog for any notable changes" in ba._get_recommendation(jwks_pr)
assert "verify callsites" in ba._get_recommendation(reached_pr)
print("ALL ASSERTIONS PASS")
