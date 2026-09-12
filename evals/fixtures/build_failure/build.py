from generator import render


if render("Sable") != "SABLE":
    raise SystemExit("BUILD ERROR: artifact must be uppercase")

print("build ok")
