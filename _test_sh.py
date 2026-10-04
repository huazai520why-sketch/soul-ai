import sys
sys.path.insert(0, r"E:\soul")
import os
os.environ["SOUL_VMINDEX"] = "0"
import soul
print("SH_RESULT_START")
print(soul.sh("wm size"))
print("SH_RESULT_END")
