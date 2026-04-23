import argparse
import asyncio
import csv
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from bleak import BleakClient

RX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


def build_start(duration=64800, extra=0):
    uuid = os.urandom(10)
    pkt = bytearray(19)
    pkt[0] = 0x23
    pkt[1] = 0x53
    pkt[2] = 0x01
    pkt[3] = 0
    pkt[4] = 0
    pkt[5:15] = uuid
    pkt[15] = (duration >> 8) & 0xFF
    pkt[16] = duration & 0xFF
    pkt[17] = (extra >> 8) & 0xFF
    pkt[18] = extra & 0xFF
    return bytes(pkt)


def parse_lap(d):
    if len(d) != 13 or d[0] != 0x23 or d[1] != 0x6C or d[-1] != 0xA5:
        return None
    return {"sid": d[3], "cnt": d[5], "pilot": d[7]}


@dataclass
class Lap:
    my_n: int
    device_n: int
    pilot: int
    t: datetime
    d: float

    def fmt(self):
        if self.d is None:
            return "-"
        m = int(self.d // 60)
        s = self.d - m * 60
        return f"{m:d}:{s:06.3f}"


def fmtt(x):
    if x is None:
        return "-"
    m = int(x // 60)
    s = x - m * 60
    return f"{m}:{s:06.3f}"


async def run(addr, out, duration, dump_skip, debounce):
    laps = []
    history = []
    bursts = [0]
    start_time = [None]
    pilot_state = {}

    print(f"Connessione a {addr}...")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "pilot", "my_lap", "dev_lap", "time_s", "fmt", "hex"])

        async with BleakClient(addr) as c:
            if not c.is_connected:
                print("Fallita.")
                return
            print("Connesso.\n")

            def h(_c, data):
                now = datetime.now()
                p = parse_lap(bytes(data))
                if not p:
                    print(f"  {now:%H:%M:%S.%f}"[:14] + f"  [info] {data.hex(' ')}")
                    return
                if start_time[0] and (now - start_time[0]).total_seconds() < dump_skip:
                    history.append(p["cnt"])
                    return
                pid = p["pilot"]
                st = pilot_state.get(pid)
                delta = (now - st["last_t"]).total_seconds() if st else None
                if delta is not None and delta < debounce:
                    bursts[0] += 1
                    return
                my_n = 1 if st is None else st["count"] + 1
                pilot_state[pid] = {"count": my_n, "last_t": now}
                lap = Lap(my_n, p["cnt"], pid, now, delta)
                laps.append(lap)
                all_t = [l.d for l in laps if l.d is not None]
                best_ovr = min(all_t) if all_t else None
                pilot_t = [l.d for l in laps if l.pilot == pid and l.d is not None]
                best_p = min(pilot_t) if pilot_t else None
                tag = ""
                if lap.d is not None:
                    if best_ovr is not None and lap.d == best_ovr:
                        tag = "*** BEST OVERALL"
                    elif best_p is not None and lap.d == best_p:
                        tag = ">> best pilota"
                print(
                    f"  {now:%H:%M:%S.%f}"[:14]
                    + f"  pilota={pid:3d}  giro={my_n:3d}  {lap.fmt():>10}  (dev#{lap.device_n:3d})  {tag}"
                )
                w.writerow(
                    [
                        now.isoformat(timespec="milliseconds"),
                        pid,
                        my_n,
                        lap.device_n,
                        f"{lap.d:.3f}" if lap.d else "",
                        lap.fmt().strip(),
                        data.hex(" "),
                    ]
                )
                f.flush()

            await c.start_notify(RX, h)
            cmd = build_start(duration)
            print(f"Invio START: {cmd.hex(' ')}")
            await c.write_gatt_char(TX, cmd, response=False)
            start_time[0] = datetime.now()
            print(f"Attendo fine dump ({dump_skip:.0f}s)...")
            await asyncio.sleep(dump_skip + 0.3)
            print(f"Dump scartato: {len(history)} passaggi.")
            print(f"Debounce per-pilota: {debounce:.1f}s\n")
            print("In ascolto. Passa i trasponder. Ctrl+C per chiudere.\n")
            try:
                while c.is_connected:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass

    print("\n" + "=" * 65)
    print(" RIEPILOGO SESSIONE")
    print("=" * 65)
    print(f"  Passaggi totali: {len(laps)}")
    print(f"  Raffiche filtrate: {bursts[0]}")
    print(f"  Storico scartato: {len(history)}")
    print()
    pilots = sorted(set(l.pilot for l in laps))
    if pilots:
        plural = "piloti" if len(pilots) != 1 else "pilota"
        print(f"  CLASSIFICA PER PILOTA ({len(pilots)} {plural}):")
        print(f"  {'Pilota':>7}  {'Giri':>5}  {'Miglior':>10}  {'Medio':>10}  {'Totale':>10}")
        print(f"  {'-'*7}  {'-'*5}  {'-'*10}  {'-'*10}  {'-'*10}")
        stats = []
        for pid in pilots:
            plaps = [l for l in laps if l.pilot == pid]
            t = [l.d for l in plaps if l.d is not None]
            b = min(t) if t else None
            a = sum(t) / len(t) if t else None
            tot = sum(t) if t else 0
            stats.append((pid, len(plaps), b, a, tot))
        stats.sort(key=lambda x: (x[2] is None, x[2] if x[2] else 9e9))
        for pid, n, b, a, tot in stats:
            print(
                f"  {pid:>7}  {n:>5}  {fmtt(b):>10}  {fmtt(a):>10}  {fmtt(tot):>10}"
            )
    timed = [l for l in laps if l.d is not None]
    if timed:
        bl = min(timed, key=lambda l: l.d)
        print(
            f"\n  >>> MIGLIOR GIRO ASSOLUTO: pilota {bl.pilot}, "
            f"giro #{bl.my_n} = {bl.fmt()}"
        )
    print("=" * 65)
    print(f"\nLog: {out}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", required=True)
    p.add_argument("--duration", type=int, default=64800)
    p.add_argument("--dump-skip", type=float, default=3.0)
    p.add_argument("--debounce", type=float, default=2.0)
    p.add_argument("--out")
    a = p.parse_args()
    out = Path(a.out) if a.out else Path.home() / f"laps_{datetime.now():%Y%m%d_%H%M%S}.csv"
    try:
        asyncio.run(run(a.address, out, a.duration, a.dump_skip, a.debounce))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
