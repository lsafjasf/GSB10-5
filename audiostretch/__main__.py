"""命令行：python3 -m audiostretch in.wav out.wav --tempo 1.25 --pitch 2"""
import argparse

from . import process, read_wav, write_wav


def main():
    ap = argparse.ArgumentParser(description='变速变调（WSOLA + 重采样），16 位 PCM WAV')
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--tempo', type=float, default=1.0,
                    help='速度倍数，>1 更快（如 1.25），<1 更慢（如 0.8）')
    ap.add_argument('--pitch', type=float, default=0.0,
                    help='变调半音数，+3 升高 3 个半音，-2 降低 2 个半音')
    ap.add_argument('--frame', type=int, default=2048)
    args = ap.parse_args()
    pitch_factor = 2.0 ** (args.pitch / 12.0)
    channels, sr = read_wav(args.input)
    out = [process(c, tempo=args.tempo, pitch=pitch_factor, frame=args.frame)
           for c in channels]
    write_wav(args.output, out, sr)
    print('已写出 %s（%d 声道, %d Hz, %d 采样点 ≈ %.2f 秒）'
          % (args.output, len(out), sr, len(out[0]) if out else 0,
             (len(out[0]) / sr) if out else 0.0))


if __name__ == '__main__':
    main()
