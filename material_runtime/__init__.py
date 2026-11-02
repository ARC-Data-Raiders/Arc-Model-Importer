"""Original native-evidence material runtime; no third-party shader data."""
from .core import Expr, MaterialRecord, MaterialPlan, expr, literal, evaluate, expression_fingerprint
from .repository import AssetRepository, parse_material, merge_records, package_ref, digest, invalidate_digest_cache
from .compiler import compile_plan
from .slots import Section, BindingResult, plan_bindings, assign_transaction, assign_many_transaction, assign_slot_transaction

__all__=['Expr','MaterialRecord','MaterialPlan','expr','literal','evaluate','expression_fingerprint','digest','invalidate_digest_cache','AssetRepository','parse_material','merge_records','package_ref','compile_plan','Section','BindingResult','plan_bindings','assign_transaction','assign_many_transaction','assign_slot_transaction']


