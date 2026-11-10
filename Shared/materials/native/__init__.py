"""Original native-evidence material runtime; no third-party shader data."""
from .core import Expr, MaterialRecord, MaterialPlan, expr, literal, evaluate, expression_fingerprint
from .repository import AssetRepository, parse_material, merge_records, package_ref, digest, invalidate_digest_cache
try:from ....Weapons.material_runtime.compiler import compile_plan
except ImportError:compile_plan=None  # top-level test import: compiler.py lives outside materials/
from .slots import Section, BindingResult, plan_bindings, assign_transaction, assign_many_transaction, assign_slot_transaction

__all__=['Expr','MaterialRecord','MaterialPlan','expr','literal','evaluate','expression_fingerprint','digest','invalidate_digest_cache','AssetRepository','parse_material','merge_records','package_ref','compile_plan','Section','BindingResult','plan_bindings','assign_transaction','assign_many_transaction','assign_slot_transaction']


