"""prop_fx: BP light/animation parsing and UE light-unit conversion (no bpy)."""
import json
import math
import os
import tempfile
import unittest

import prop_fx as pf

BP = [
    {'Type': 'SCS_Node', 'Name': 'SCS_Node_0', 'Properties': {'InternalVariableName': 'DefaultSceneRoot', 'ChildNodes': [
        {'ObjectPath': '/Game/X/BP.2'}]}},
    {'Type': 'SCS_Node', 'Name': 'SCS_Node_7', 'Properties': {'InternalVariableName': 'WarningLight1', 'ComponentTemplate': {
        'ObjectName': "SpotLightComponent'BP_C:WarningLight1_GEN_VARIABLE'", 'ObjectPath': '/Game/X/BP.3'}}},
    {'Type': 'SCS_Node', 'Name': 'SCS_Node_1', 'Properties': {'InternalVariableName': 'Fx', 'ComponentTemplate': {
        'ObjectName': "NiagaraComponent'BP_C:Fx_GEN_VARIABLE'", 'ObjectPath': '/Game/X/BP.4'}}},
    {'Type': 'SpotLightComponent', 'Name': 'WarningLight1_GEN_VARIABLE', 'Properties': {
        'OuterConeAngle': 20.0, 'bUseInverseSquaredFalloff': False, 'AttenuationRadius': 900.0, 'Intensity': 100.0,
        'RelativeLocation': {'X': 936.76, 'Y': -366.76, 'Z': 227.29}, 'RelativeRotation': {'Pitch': 0.0, 'Yaw': -45.0, 'Roll': 0.0}}},
    {'Type': 'NiagaraComponent', 'Name': 'Fx_GEN_VARIABLE', 'Properties': {'Asset': {'ObjectPath': '/Game/Pioneer/FX/NS_A.0'}}},
    {'Type': 'SimpleAnimationSequenceComponent', 'Name': 'Lights', 'Properties': {
        'AnimationPlaybackMode': 'EAnimationPlaybackMode::Loop', 'AnimationTracks': [{
            'ComponentPicker': {'SanitizedComponentName': 'WarningLight1'}, 'AnimationTrackApplicationMode': 'EAnimationTrackApplicationMode::AdditiveLocal',
            'CurveTranslateRotate': {'ObjectPath': '/Game/Pioneer/Core/C_Rot.0'}}]}},
    {'Type': 'SimpleAnimationSequenceComponent', 'Name': 'Door', 'Properties': {'AnimationTracks': [{
        'ComponentPicker': {'SanitizedComponentName': 'Door'}, 'CurveTranslateRotate': {'ObjectPath': '/Game/Pioneer/Core/C_Door.0'}}]}},
]


class PropFxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bp = os.path.join(self.tmp.name, 'BP.json')
        with open(self.bp, 'w', encoding='utf-8') as fh:
            json.dump(BP, fh)
        os.makedirs(os.path.join(self.tmp.name, 'Core'))
        with open(os.path.join(self.tmp.name, 'Core', 'C_Rot.json'), 'w', encoding='utf-8') as fh:
            json.dump([{'Type': 'CurveTranslateRotate', 'Properties': {'FloatCurves[5]': {'Keys': [
                {'Time': 0.0, 'Value': 0.0}, {'Time': 1.0, 'Value': 360.0}]}}}], fh)
        with open(os.path.join(self.tmp.name, 'Core', 'C_Door.json'), 'w', encoding='utf-8') as fh:
            json.dump([{'Type': 'CurveTranslateRotate', 'Properties': {'FloatCurves': {'Keys': [  # element 0: bare name
                {'Time': 0.0, 'Value': 0.0}, {'Time': 0.8, 'Value': -350.0}]}}}], fh)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse(self):
        fx = pf.parse_bp_fx(self.bp)
        self.assertEqual([r['var'] for r in fx['lights']], ['WarningLight1'])
        self.assertEqual(fx['lights'][0]['rotation'], (0.0, -45.0, 0.0))
        self.assertEqual([r['var'] for r in fx['niagara']], ['Fx'])
        self.assertEqual([t['component'] for t in fx['tracks']], ['WarningLight1'])  # door is not looping

    def test_curve(self):
        path = pf.resolve_game_path('/Game/Pioneer/Core/C_Rot.0', self.tmp.name)
        self.assertEqual(pf.load_translate_rotate(path), {5: [(0.0, 0.0, 'RCIM_Linear'), (1.0, 360.0, 'RCIM_Linear')]})
        path = pf.resolve_game_path('/Game/Pioneer/Core/C_Door.0', self.tmp.name)
        self.assertEqual(pf.load_translate_rotate(path), {0: [(0.0, 0.0, 'RCIM_Linear'), (0.8, -350.0, 'RCIM_Linear')]})

    def test_watts(self):
        # Lumens spot: P = 2 * lm / (1 - cos(outer))
        p = {'IntensityUnits': 'ELightUnits::Lumens', 'Intensity': 10.0, 'OuterConeAngle': 55.0}
        self.assertAlmostEqual(pf.ue_light_watts(p, 'SPOT'), 20.0 / (1 - math.cos(math.radians(55))), 6)
        self.assertAlmostEqual(pf.ue_light_watts({'IntensityUnits': 'ELightUnits::Candelas', 'Intensity': 1.0}, 'POINT'), 4 * math.pi, 6)
        self.assertAlmostEqual(pf.ue_light_watts({'Intensity': 100.0}, 'POINT'), 1600 / 1e4 * 4 * math.pi, 6)
        # non-inverse-square: raw Intensity with (1 - (d/R)^2)^exp falloff, matched at 0.3 * radius (m)
        w = pf.ue_light_watts({'Intensity': 100.0, 'bUseInverseSquaredFalloff': False, 'AttenuationRadius': 900.0,
                               'LightFalloffExponent': 4.0}, 'SPOT')
        self.assertAlmostEqual(w, 100.0 * 0.91 ** 4 * 2.7 ** 2 * 4 * math.pi, 6)


if __name__ == '__main__':
    unittest.main()
