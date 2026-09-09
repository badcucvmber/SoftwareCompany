try:
    import metagpt
    print("MetaGPT found")
except ImportError:
    print("MetaGPT missing")

try:
    import fire
    print("Fire found")
except ImportError:
    print("Fire missing")
