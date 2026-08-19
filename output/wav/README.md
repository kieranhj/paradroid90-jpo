# Rendered audio

Not checked in -- it is ~60 MB of WAV that any checkout can regenerate in a
few seconds. From the repo root, with

    MOD=input/extracted/Paradroid_90/Custom_Version/cust.Paradroid_90

the Amiga renders are

    for s in 1 2 3 4 5; do
      python tools/render.py $MOD -s $s --loops 0 -o output/wav/out_sub$s.wav
    done

and the BBC Micro previews (SN76489 emulation of the converted VGMs) are

    for s in 1 2 3 4 5; do
      python tools/vgmrender.py output/vgm/paradroid90_sub$s.vgm \
             -o output/wav/sn_sub$s.wav
    done
