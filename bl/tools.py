import argparse, json, os
from .core.crypto import new_keypair, priv_to_hex, pub_to_hex, pubkey_to_address


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int)
    ap.add_argument("--out", default="genesis")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    vs = []
    for i in range(a.n):
        sk, vk = new_keypair()
        addr = pubkey_to_address(vk)
        with open(os.path.join(a.out, f"validator-{i}.priv"),"w") as f:
            f.write(priv_to_hex(sk))
        os.chmod(os.path.join(a.out, f"validator-{i}.priv"), 0o600)
        vs.append({"address":addr,"pubkey":pub_to_hex(vk),"name":f"v{i}"})
        print(f"v{i}: {addr}")
    with open(os.path.join(a.out,"genesis.json"),"w") as f:
        json.dump({"validators":vs}, f, indent=2)


if __name__ == "__main__": main()