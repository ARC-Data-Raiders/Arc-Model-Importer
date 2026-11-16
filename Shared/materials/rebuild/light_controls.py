"""Optional Blender timeline pulse, independent of the native static light equations."""
import math


def pulse_intensity(g, intensity, record):
    scalars = record['scalars']
    enabled = record.get('switches', {}).get('Pulsating', False)
    amount = min(max(float(scalars.get('PulseAmount', 1. if enabled else 0.)), 0.), 1.)
    if not enabled and amount == 0.:
        return intensity
    strength = g.clamp(g.parameter('Pulse Amount', amount))
    speed = g.math('MAXIMUM', g.parameter('Pulse Speed', scalars.get('PulseSpeed', 1.)), 0.)
    phase = g.parameter('Pulse Phase', scalars.get('PulsePhase', 0.))
    seconds = g.time('Light time (seconds)')
    angle = g.add(g.mul(g.mul(seconds, speed), 2. * math.pi), phase)
    wave = g.add(g.mul(g.math('SINE', angle), .5), .5)
    g.report['warnings'].append('approximate: timeline-driven sinusoidal light pulse; native noise flicker is not reconstructed')
    return g.mul(intensity, g.mix(1., wave, strength))
