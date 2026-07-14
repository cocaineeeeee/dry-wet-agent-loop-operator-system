"""Standalone plate-reader simulator (construct B of W4).

Run it as its own process::

    python -m expos.adapters.wet.sim_reader --port 8765 [--seed 7] [--noise 0.02]

Wire protocol: **TCP loopback socket, newline-delimited JSON** (one JSON object
per request line, one per response line). See INTEGRATION.md for the socket-vs-
file-mailbox justification; in short, a live socket is the only faithful model of
the four device behaviours G3 must exercise -- *offline* (connection refused),
*slow* / *no response* (client-side timeout), *process kill* (reset), and
*health answers while a measurement is in flight* -- none of which a polled file
mailbox can represent without re-implementing a socket badly.

The seven G3 concerns, device side:
  * health         -- GET-like {status, uptime, last_calibration, meas_count}
  * calibration    -- calibrate() resets the gain/offset drift model
  * reservation    -- single lease; acquire(ttl)/release; TTL auto-expiry
  * protocol valid.-- (lives in ot_protocol; reader rejects unlabeled samples)
  * timeout/retry  -- injectable slow / no-response; client times out (driver)
  * device failure -- injectable offline/error-code/partial-dropout; kill=reset
  * sample identity-- every reading echoes its sample_id; unlabeled = rejected

Hidden truth surface (solvent_screen): response is a unimodal Gaussian in solvent
polarity plus measurement noise. The truth (true_response, applied gain/offset,
artifact tags) lives ONLY in the server's internal sidecar; clients never receive
it. Two artefact injectors are built in (M16): calibration drift + random dropout;
edge/batch injectors can be added later behind the same hook.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any


# --- hidden truth surface ------------------------------------------------------

#: Named truth-surface profiles -> peak polarity (mu). A SIGNAL profile fixes ONLY
#: the peak location; amplitude/sigma/baseline (hence the whole statistical shape and
#: the injected-noise structure) are IDENTICAL across signal profiles. A signal
#: profile therefore flips the SIGN of the response-vs-polarity relation WITHOUT
#: changing any other statistical property -- the clean K1 discriminator (M17 K-D).
#:
#:   * "polar_high"    -- peak at mu=0.55 (the M16 default face): mid/high-polarity
#:                        solvents respond higher; consistent with the seeded
#:                        "polar-higher supported" ledger claim.
#:   * "nonpolar_high" -- peak at mu=0.20 (just below the measurable target-polarity
#:                        window's low edge ~0.30): response DECREASES monotonically
#:                        across the 8-solvent window, so LOW-polarity solvents
#:                        (hexane/toluene) respond highest; CONTRADICTS the seeded
#:                        "polar-higher" claim (nonpolar-higher is the true face).
#:   * "flat"          -- the NULL face (MR_null, first realised form: no signal).
#:                        The response is INDEPENDENT of polarity: zero amplitude
#:                        collapses the Gaussian to the constant baseline, so every
#:                        solvent shares one true mean. The measurement-noise
#:                        structure (gauss(0, noise_sd)) and every other statistical
#:                        property are UNCHANGED -- only the signal is removed. A
#:                        correct aggregator must return "insufficient" on this face
#:                        rather than fabricate a direction claim. (Randomized /
#:                        shuffled null faces are left for K-E to add as needed.)
#:   * "polar_high_strong" -- the SUPPORTED-path face (letter 093 ruling (b)).
#:                        polar_high's mu=0.55 sits almost equidistant between the
#:                        two live-promoted arms' realised polarities (ethanol
#:                        0.5925 / acetonitrile 0.507): a GENUINE ~0 effect, so that
#:                        face honestly never adjudicates the eth-vs-acn contrast
#:                        (K-E structural finding). Lifting ONLY mu to 0.70 puts the
#:                        pair on a steep flank (response diff ~ +0.34 x amplitude):
#:                        the seeded "polar-higher" claim can reach a decisive
#:                        SUPPORTED. polar_high itself stays bit-for-bit (M16
#:                        regression anchor); joint-run condition "consistent-strong".
#:   * "catalyst_high" -- the M20 second-domain (catalyst_screen) SIGNAL face. The
#:                        response surface is domain-AGNOSTIC (a 1D Gaussian in the
#:                        realised normalized coordinate, whatever it means physically);
#:                        only mu moves. mu=0.85 sits just ABOVE the mixable window's
#:                        high edge (realised ~0.75), so the response INCREASES
#:                        monotonically across the window: HIGH-descriptor catalysts
#:                        respond highest (positive corr(coord, response)) -- the
#:                        catalyst analogue of the seeded "higher-coord-wins" claim.
#:                        It is the exact mirror of ``nonpolar_high`` (mu=0.20, just
#:                        below the LOW edge -> monotonic decreasing). amplitude/sigma/
#:                        baseline are IDENTICAL to every solvent signal face, so the
#:                        only-mu-differs K-D law holds ACROSS domains -- proving the
#:                        truth surface is a reusable domain-neutral runtime piece, not
#:                        a solvent special case. (A ``catalyst_low`` flipped face is a
#:                        one-line mu addition when the K-D flip suite is grown; the
#:                        polarity-independent NULL face ``flat`` already serves both
#:                        domains unchanged.)
#:   * "catalyst_low"  -- the M20 FLIPPED face (K-D flip suite, machine-debt item #1
#:                        cleared): mu=0.20 sits just BELOW the catalyst window's low
#:                        realised edge (~0.32), so the response DECREASES monotonically
#:                        across the window -- LOW-descriptor ligands respond highest,
#:                        CONTRADICTING the seeded "high-coord-wins" claim. Exact
#:                        catalyst analogue of ``nonpolar_high`` (same mu by design:
#:                        the law fixes only-mu-differs, not mu-uniqueness).
#:   * "expression_high" -- the M24 THIRD-domain (cell_free_expression_screen) SIGNAL
#:                        face. Same domain-AGNOSTIC 1D Gaussian in the realised
#:                        construct design coordinate; only mu moves. mu=0.85 sits just
#:                        ABOVE the mixable window's high edge (realised ~0.75), so the
#:                        response INCREASES monotonically across the window: HIGH-design
#:                        constructs (stronger promoter/RBS) express highest (positive
#:                        corr(coord, response)) -- the biological analogue of the seeded
#:                        "higher-design-wins" claim. QUANTITATIVE BASIS for choosing a
#:                        monotone-rising (not internal-unimodal) face: the Anderson
#:                        promoter collection's public relative strengths span ~0.03->1.0
#:                        (~30x, monotone, no interior peak; iGEM Registry) -- the window
#:                        gives ~16x dynamic range, same量级. mu coincides with
#:                        ``catalyst_high`` (0.85) BY DESIGN: it re-proves only-mu-differs
#:                        holds across chemistry<->biology, so the truth surface is a
#:                        reusable domain-neutral runtime piece, not a chemistry special
#:                        case. (INDEX_M24_CFEXPR §1; A=1.0/sigma=0.15/baseline=0.05
#:                        identical to every signal face.)
#:   * "expression_flipped" -- the M24 FLIPPED face (K-D flip suite for the biological
#:                        domain): mu=0.20 sits just BELOW the window's low realised edge
#:                        (~0.32), so the response DECREASES monotonically -- LOW-design
#:                        constructs express highest, CONTRADICTING the seeded
#:                        "higher-design-wins" claim. Exact biological analogue of
#:                        ``catalyst_low`` / ``nonpolar_high`` (same mu by design; the law
#:                        fixes only-mu-differs, not mu-uniqueness). The polarity-
#:                        independent NULL face ``flat`` already serves this domain
#:                        unchanged (cross-domain shared null, one source of truth).
TRUTH_PROFILES: dict[str, float] = {
    "polar_high": 0.55,
    "nonpolar_high": 0.20,
    "flat": 0.55,
    "polar_high_strong": 0.70,
    "catalyst_high": 0.85,
    "catalyst_low": 0.20,
    "expression_high": 0.85,
    "expression_flipped": 0.20,
}
#: Profiles whose amplitude is zeroed -> a polarity-independent (null) truth face.
#: Kept separate so the SIGN-flip signal profiles keep the "only mu differs" law.
_NULL_PROFILES: frozenset[str] = frozenset({"flat"})
DEFAULT_TRUTH_PROFILE = "polar_high"


@dataclass
class TruthSurface:
    """Ground-truth response = A * exp(-(p-mu)^2 / 2 sigma^2) + baseline.

    Unimodal in polarity; the optimum sits at ``mu``. Never leaves the server.
    """

    amplitude: float = 1.0
    mu: float = 0.55
    sigma: float = 0.15
    baseline: float = 0.05

    @classmethod
    def from_profile(cls, profile: str = DEFAULT_TRUTH_PROFILE) -> TruthSurface:
        """Build a surface from a named profile.

        For SIGNAL profiles only the peak location (mu) differs; the default
        ``polar_high`` maps to mu=0.55 == the dataclass default, so the M16
        behaviour is reproduced bit-for-bit. A NULL profile (``flat``) additionally
        zeroes the amplitude, collapsing the surface to the constant baseline -- a
        polarity-INDEPENDENT (no-signal) face -- while leaving sigma/baseline (and
        the caller's noise structure) untouched. An unknown profile fails LOUDLY
        (never a silent fallback to the default face -- a mis-wired discriminator
        must not masquerade as the normal surface).
        """
        if profile not in TRUTH_PROFILES:
            raise ValueError(
                f"unknown truth_profile {profile!r}; known: {sorted(TRUTH_PROFILES)}"
            )
        if profile in _NULL_PROFILES:
            # null face: amplitude 0 => response == baseline for every polarity.
            return cls(amplitude=0.0, mu=TRUTH_PROFILES[profile])
        return cls(mu=TRUTH_PROFILES[profile])

    def response(self, polarity: float) -> float:
        z = (polarity - self.mu) / self.sigma
        return self.amplitude * math.exp(-0.5 * z * z) + self.baseline


# --- reader state --------------------------------------------------------------

@dataclass
class Lease:
    lease_id: str
    holder: str
    ttl_s: float
    acquired_at: float

    def expired(self, now: float) -> bool:
        return now > self.acquired_at + self.ttl_s


@dataclass
class FaultConfig:
    """Injectable device faults (set via the ``inject`` admin command)."""

    status: str = "healthy"          # healthy | degraded | offline
    slow_ms: float = 0.0             # per-measure added latency
    hang: bool = False               # measure never responds (client must time out)
    error_next: int = 0              # return an error code for the next N measures
    error_code: str = "E_DEVICE"     # code to return when error_next fires
    dropout_prob: float = 0.0        # per-well probability a reading is dropped
    dropout_wells: list[str] = field(default_factory=list)  # forced dropouts
    #: persistent per-well device error {well_id: code} -- survives client retries
    #: (a well that errors every time until the fault is cleared). Mirrors
    #: dropout_wells; drives the recovery-policy tests (E_DEVICE / E_SENSOR / an
    #: undefined code) without racing the transient error_next counter.
    error_wells: dict[str, str] = field(default_factory=dict)
    #: PLATE-level constant additive offset {plate_id: offset} (M24 §4 plate_offset
    #: fault). Distinct in FORM from calibration_drift: drift is a per-well MONOTONIC
    #: accrual (gain/offset step every measured well), whereas this is a per-PLATE
    #: CONSTANT step (plateA +0.08 / plateB -0.04 ...) applied identically to every well
    #: on a plate regardless of measurement order. A sample declares its plate via an
    #: optional ``plate_id`` field; absent/unknown plate -> zero offset. Magnitude anchor:
    #: CFPS batch CV (mild 5-10% / bad 30-40%, ACS Synth. Biol. 9b00178; INDEX_M24_CFEXPR
    #: §3). Truth isolation is unchanged: the OS reading carries the corrupted value but
    #: NO plate_offset field; the truth sidecar records the injected offset + tag.
    plate_offsets: dict[str, float] = field(default_factory=dict)

    def clear(self) -> None:
        self.status = "healthy"
        self.slow_ms = 0.0
        self.hang = False
        self.error_next = 0
        self.dropout_prob = 0.0
        self.dropout_wells = []
        self.error_wells = {}
        self.plate_offsets = {}


class ReaderState:
    """All mutable device state, guarded by ``self.lock``.

    Calibration drift model: reading = truth * gain + offset + noise. Each
    measured well nudges gain down and offset up by a fixed step; ``calibrate``
    resets them. An uncalibrated instrument therefore imparts a growing, well
    ordered systematic bias -- detectable by re-reading control samples.
    """

    #: drift per measured well (calibration artefact injector)
    GAIN_DRIFT = 0.006
    OFFSET_DRIFT = 0.004

    def __init__(
        self,
        seed: int = 0,
        noise_sd: float = 0.02,
        truth_profile: str = DEFAULT_TRUTH_PROFILE,
    ) -> None:
        self.lock = threading.RLock()
        self.rng = random.Random(seed)
        self.noise_sd = noise_sd
        self.truth_profile = truth_profile
        self.truth = TruthSurface.from_profile(truth_profile)
        self.started_at = time.time()
        self.meas_count = 0
        self.reading_seq = 0
        self.gain = 1.0
        self.offset = 0.0
        self.last_calibration_meas = 0
        self.last_calibration_at = self.started_at
        self.lease: Lease | None = None
        self.faults = FaultConfig()
        #: truth sidecar -- server-only; exposed to tests via the admin dump cmd
        self.truth_records: list[dict[str, Any]] = []
        self._lease_counter = 0

    # -- reservation ----------------------------------------------------------

    def _reap_lease(self, now: float) -> None:
        if self.lease is not None and self.lease.expired(now):
            self.lease = None

    def acquire(self, holder: str, ttl_s: float) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            self._reap_lease(now)
            if self.lease is not None:
                return {
                    "ok": False, "error": "resource_busy",
                    "detail": f"held by {self.lease.holder!r} until "
                              f"{self.lease.acquired_at + self.lease.ttl_s:.3f}",
                }
            self._lease_counter += 1
            self.lease = Lease(
                lease_id=f"lease-{self._lease_counter}",
                holder=holder, ttl_s=ttl_s, acquired_at=now,
            )
            return {"ok": True, "lease_id": self.lease.lease_id,
                    "expires_at": now + ttl_s}

    def release(self, lease_id: str) -> dict[str, Any]:
        with self.lock:
            if self.lease is None:
                return {"ok": False, "error": "no_lease"}
            if self.lease.lease_id != lease_id:
                return {"ok": False, "error": "lease_mismatch"}
            self.lease = None
            return {"ok": True}

    def _check_lease(self, lease_id: str | None) -> str | None:
        now = time.time()
        self._reap_lease(now)
        if self.lease is None:
            return "no_lease"
        if lease_id != self.lease.lease_id:
            return "lease_invalid"
        return None

    # -- health / calibration -------------------------------------------------

    def health(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            self._reap_lease(now)
            return {
                "ok": True,
                "status": self.faults.status,
                "uptime_s": round(now - self.started_at, 3),
                "last_calibration": {
                    "at_meas": self.last_calibration_meas,
                    "at_time": round(self.last_calibration_at, 3),
                    "meas_since": self.meas_count - self.last_calibration_meas,
                },
                "meas_count": self.meas_count,
                "lease_held": self.lease is not None,
            }

    def calibrate(self, lease_id: str | None) -> dict[str, Any]:
        with self.lock:
            bad = self._check_lease(lease_id)
            if bad:
                return {"ok": False, "error": bad}
            self.gain = 1.0
            self.offset = 0.0
            self.last_calibration_meas = self.meas_count
            self.last_calibration_at = time.time()
            return {"ok": True, "gain": self.gain, "offset": self.offset,
                    "at_meas": self.meas_count}

    # -- measurement ----------------------------------------------------------

    def prepare_measure(
        self, lease_id: str | None, samples: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, float, bool]:
        """Compute readings under lock; return (response, slow_s, hang).

        The slow/hang latency is applied by the caller OUTSIDE the lock so a
        concurrent health check stays responsive during a slow measurement.
        """
        with self.lock:
            if self.faults.status == "offline":
                return {"ok": False, "error": "device_offline"}, 0.0, False
            bad = self._check_lease(lease_id)
            if bad:
                return {"ok": False, "error": bad}, 0.0, False
            if not isinstance(samples, list) or not samples:
                return {"ok": False, "error": "no_samples"}, 0.0, False

            # error-code injection (device-failure handling)
            if self.faults.error_next > 0:
                self.faults.error_next -= 1
                return (
                    {"ok": False, "error": "device_error",
                     "code": self.faults.error_code},
                    0.0, False,
                )

            # persistent per-well error injection (recovery-policy driver)
            if self.faults.error_wells:
                for s in samples:
                    code = self.faults.error_wells.get(s.get("well_id", ""))
                    if code is not None:
                        return (
                            {"ok": False, "error": "device_error", "code": code},
                            0.0, False,
                        )

            hang = self.faults.hang
            slow_s = self.faults.slow_ms / 1000.0

            readings: list[dict[str, Any]] = []
            for s in samples:
                sid = s.get("sample_id")
                # sample-identity enforcement: device refuses unlabeled samples
                if not sid:
                    return (
                        {"ok": False, "error": "missing_sample_id",
                         "detail": f"sample without sample_id: {s}"},
                        0.0, False,
                    )
                well_id = s.get("well_id", "")
                polarity = float(s.get("polarity", 0.0))
                # plate-level constant additive offset (M24 §4): a per-PLATE step, NOT a
                # per-well accrual. The sample optionally declares its plate via plate_id;
                # absent/unknown plate -> zero offset (existing solvent path is untouched).
                plate_id = s.get("plate_id", "")
                plate_offset = float(self.faults.plate_offsets.get(plate_id, 0.0))

                # dropout injection (partial-reading device failure / artefact)
                forced = well_id in self.faults.dropout_wells
                probabilistic = self.rng.random() < self.faults.dropout_prob
                dropped = forced or probabilistic

                self.meas_count += 1
                self.reading_seq += 1
                # calibration drift accrues per measured well
                self.gain -= self.GAIN_DRIFT
                self.offset += self.OFFSET_DRIFT

                true_resp = self.truth.response(polarity)
                noise = self.rng.gauss(0.0, self.noise_sd)
                artifacts: list[str] = []
                if self.meas_count - self.last_calibration_meas > 0:
                    # bias present whenever we have drifted since last calibration
                    if abs(self.gain - 1.0) > 1e-9 or abs(self.offset) > 1e-9:
                        artifacts.append("calibration_drift")
                value: float | None
                if dropped:
                    value = None
                    artifacts.append("dropout")
                else:
                    # plate_offset is a board-level CONSTANT added on top of the drift
                    # model -- the corrupted value reaches the OS; the offset truth does not.
                    value = true_resp * self.gain + self.offset + noise + plate_offset
                    if plate_offset != 0.0:
                        artifacts.append("plate_offset")

                # truth sidecar (server-only)
                self.truth_records.append({
                    "seq": self.reading_seq, "sample_id": sid, "well_id": well_id,
                    "polarity": polarity, "true_response": true_resp,
                    "gain": self.gain, "offset": self.offset, "noise": noise,
                    "plate_id": plate_id, "plate_offset": plate_offset,
                    "value": value, "artifacts": artifacts,
                })
                # OS-visible reading -- NO truth fields
                reading = {
                    "sample_id": sid, "well_id": well_id, "seq": self.reading_seq,
                    "value": None if value is None else round(value, 6),
                    "status": "dropout" if dropped else "ok",
                }
                if self.faults.status == "degraded":
                    reading["quality"] = "degraded"
                readings.append(reading)

            resp = {"ok": True, "readings": readings,
                    "device_status": self.faults.status}
            return resp, slow_s, hang


# --- socket server -------------------------------------------------------------

class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: ReaderState = self.server.state  # type: ignore[attr-defined]
        for raw in self.rfile:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send({"ok": False, "error": "bad_json", "detail": str(exc)})
                continue
            try:
                resp = self._dispatch(state, req)
            except Exception as exc:  # never crash the connection silently
                resp = {"ok": False, "error": "internal",
                        "detail": f"{type(exc).__name__}: {exc}"}
            if resp is None:  # shutdown / hang handled inline
                return
            self._send(resp)

    def _send(self, obj: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _dispatch(self, state: ReaderState, req: dict[str, Any]):
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True, "pong": True}
        if cmd == "capabilities":
            # device-side capability manifest, sourced from the external labware
            # definition (Q3 "structure + manifest"): the reader declares what
            # plate it can read without the client hard-coding an 8x12 assumption.
            from .labware import load_labware
            lw = load_labware()
            return {"ok": True, "channels": lw.well_channels,
                    "labware": lw.capabilities(), "metric": "solvent_response"}
        if cmd == "health":
            return state.health()
        if cmd == "acquire":
            return state.acquire(req.get("holder", "anon"),
                                 float(req.get("ttl", 30.0)))
        if cmd == "release":
            return state.release(req.get("lease_id"))
        if cmd == "calibrate":
            return state.calibrate(req.get("lease_id"))
        if cmd == "measure":
            resp, slow_s, hang = state.prepare_measure(
                req.get("lease_id"), req.get("samples", [])
            )
            if hang:
                # model a non-responding device: sleep well past any client
                # timeout, then drop the connection without replying.
                time.sleep(float(req.get("_hang_s", 3600.0)))
                return None
            if slow_s > 0:
                time.sleep(slow_s)
            return resp
        # --- admin / test-only commands (a real device would not expose these) --
        if cmd == "inject":
            return self._inject(state, req)
        if cmd == "truth_dump":  # truth sidecar readout, tests only
            with state.lock:
                return {"ok": True, "truth_records": list(state.truth_records),
                        "gain": state.gain, "offset": state.offset}
        if cmd == "reset_faults":
            with state.lock:
                state.faults.clear()
            return {"ok": True}
        if cmd == "shutdown":
            self._send({"ok": True, "shutting_down": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return {"ok": False, "error": "unknown_cmd", "cmd": cmd}

    @staticmethod
    def _inject(state: ReaderState, req: dict[str, Any]) -> dict[str, Any]:
        with state.lock:
            f = state.faults
            if "status" in req:
                if req["status"] not in ("healthy", "degraded", "offline"):
                    return {"ok": False, "error": "bad_status"}
                f.status = req["status"]
            if "slow_ms" in req:
                f.slow_ms = float(req["slow_ms"])
            if "hang" in req:
                f.hang = bool(req["hang"])
            if "error_next" in req:
                f.error_next = int(req["error_next"])
            if "error_code" in req:
                f.error_code = str(req["error_code"])
            if "dropout_prob" in req:
                f.dropout_prob = float(req["dropout_prob"])
            if "dropout_wells" in req:
                f.dropout_wells = list(req["dropout_wells"])
            if "error_wells" in req:
                f.error_wells = dict(req["error_wells"])
            if "plate_offsets" in req:
                f.plate_offsets = {str(k): float(v)
                                   for k, v in dict(req["plate_offsets"]).items()}
            if req.get("clear"):
                f.clear()
            return {"ok": True, "faults": {
                "status": f.status, "slow_ms": f.slow_ms, "hang": f.hang,
                "error_next": f.error_next, "dropout_prob": f.dropout_prob,
                "dropout_wells": f.dropout_wells, "error_wells": f.error_wells,
                "plate_offsets": f.plate_offsets,
            }}


class ReaderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, state: ReaderState) -> None:
        super().__init__((host, port), _Handler)
        self.state = state


def serve(host: str = "127.0.0.1", port: int = 8765,
          seed: int = 0, noise_sd: float = 0.02,
          truth_profile: str = DEFAULT_TRUTH_PROFILE) -> ReaderServer:
    """Create and return a bound server (call ``.serve_forever()`` to run).

    ``truth_profile`` selects the hidden truth face (default ``polar_high`` == the
    M16 surface). Pass ``nonpolar_high`` for the K1 discriminator domain, where the
    true response contradicts the seeded "polar-higher" claim (M17 K-D).
    """
    state = ReaderState(seed=seed, noise_sd=noise_sd, truth_profile=truth_profile)
    return ReaderServer(host, port, state)


def main() -> None:
    ap = argparse.ArgumentParser(description="plate-reader simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--noise", type=float, default=0.02)
    ap.add_argument("--truth-profile", default=DEFAULT_TRUTH_PROFILE,
                    choices=sorted(TRUTH_PROFILES),
                    help="hidden truth face (default polar_high == M16 surface)")
    args = ap.parse_args()
    srv = serve(args.host, args.port, args.seed, args.noise, args.truth_profile)
    actual_port = srv.server_address[1]
    print(f"sim_reader listening on {args.host}:{actual_port} "
          f"(seed={args.seed}, noise={args.noise}, "
          f"truth_profile={args.truth_profile})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("reader: interrupted, shutting down", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()


# ---- scoring-harness-only truth harvest (moved from bridge.py: EXP004 producer role) ----
def harvest_truth(
    host: str = "127.0.0.1", port: int = 8765, timeout: float = 5.0
) -> list[dict[str, Any]]:
    """SCORING-HARNESS ONLY: pull the reader's hidden truth sidecar.

    NOT part of the OS decision path. The returned records are what a post-hoc
    scoring/attribution harness would hand to ``store.save_truth`` for opaque
    sidecar persistence. Never call this from qc/models/planner/agent.
    """
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"cmd": "truth_dump"}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("reader closed without truth reply")
            buf += chunk
    reply = json.loads(buf.split(b"\n", 1)[0].decode())
    return reply.get("truth_records", [])

