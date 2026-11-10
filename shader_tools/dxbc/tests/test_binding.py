import json
import os
import pathlib
import re
import unittest
from .. import binding

W1 = pathlib.Path(os.environ['DR_WAVE1_RUN'])


class BindingCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = []
        for run in ['slice', 'global']:
            root = W1 / 'out/W1' / run
            cls.records.extend((run, root, record) for record in json.loads((root / 'shader-records.manifest.json').read_text())['records'])
        cls.uniform = binding._uniform_sets(W1 / 'out/W2/full')
        cls.counts = {}

    def test_1_all_trailers(self):
        count = 0
        for _, root, record in self.records:
            raw = (root / record['record_file']).read_bytes()
            self.assertIsNotNone(binding.parse_trailer(raw[record['container_offset']+record['container_size']:]))
            count += 1
        self.assertEqual(count, 17774)
        self.counts['trailers_parsed'] = count

    def test_2_sm5_headers(self):
        count = 0
        for _, root, record in self.records:
            container = (root / record['container_file']).read_bytes()
            if binding.classify(container) == 'sm5':
                raw = (root / record['record_file']).read_bytes()
                self.assertIsNotNone(binding.parse_header(raw[:record['container_offset']]))
                count += 1
        self.counts['sm5_headers_parsed'] = count

    def test_3_slice_declarations(self):
        seen = set()
        for run, root, record in self.records:
            sha = record['container_sha256']
            parsed = W1 / 'out/W7/slice' / (sha+'.json')
            if run != 'slice' or not parsed.exists() or sha in seen:
                continue
            seen.add(sha)
            data = binding.bindings((root / record['record_file']).read_bytes(), record['container_offset'], record['container_size'], self.uniform.get(record['map_hash']))
            for declaration in json.loads(parsed.read_text())['declarations']:
                if declaration['op'].startswith('dcl_resource'):
                    for slot in re.findall(r'\bt(\d+)\b', declaration['text']):
                        self.assertIn(slot, data['t'])
                if declaration['op'] == 'dcl_sampler':
                    for slot in re.findall(r'\bs(\d+)\b', declaration['text']):
                        self.assertIn(slot, data['s'])
        self.assertEqual(len(seen), 66)
        self.counts['unique_slice_sm5_declarations_covered'] = len(seen)

    def test_4_material_rule(self):
        count = 0
        sm5_count = 0
        for run, root, record in self.records:
            if run != 'slice':
                continue
            data = binding.bindings((root / record['record_file']).read_bytes(), record['container_offset'], record['container_size'], self.uniform.get(record['map_hash']))
            if 'Material' in data['cb'].values():
                self.assertIn(record['map_hash'], self.uniform)
                count += 1
                sm5_count += binding.classify((root / record['container_file']).read_bytes()) == 'sm5'
        self.assertEqual(count, 95)
        self.assertEqual(sm5_count, 41)
        self.counts['slice_material_rule_passed'] = count
        self.counts['slice_sm5_material_rule_passed'] = sm5_count

    def test_5_example(self):
        matches = []
        for run, root, record in self.records:
            if run == 'slice' and record['map_hash'].startswith('A0D111A4') and record['shader_index'] == 1617 and binding.classify((root / record['container_file']).read_bytes()) == 'sm5':
                matches.append(binding.bindings((root / record['record_file']).read_bytes(), record['container_offset'], record['container_size'], self.uniform[record['map_hash']]))
        self.assertEqual(len(matches), 1)
        data = matches[0]
        self.assertEqual(data['t']['1']['param'], 'Base_Material_NOH')
        self.assertEqual(data['t']['2']['param'], 'Breakup_Material_NOH')
        self.assertEqual(data['t']['3']['param'], 'Breakup Mask - Linear Grayscale')
        self.assertEqual((data['t']['0']['ub'], data['t']['0']['res']), ('Scene', 2))
        self.counts['example_passed'] = True

    @classmethod
    def tearDownClass(cls):
        pathlib.Path(os.environ['DR_BINDING_COUNTS']).write_text(json.dumps(cls.counts, indent=2)+'\n', encoding='utf-8', newline='\n')
