"""Exact native-table to exported-section material binding with atomic assignment."""
import threading
from dataclasses import dataclass
from collections.abc import Mapping

@dataclass(frozen=True)
class Section:
    section_index: int
    material_index: int
    slot_index: int
    lod: int = 0

@dataclass(frozen=True)
class BindingResult:
    assignments: tuple
    diagnostics: tuple


def plan_bindings(material_table,sections,selected_lod=0,explicit=None):
    """Return (slot, exact material identity) for every selected render section.

    explicit keys are section indices, not material-table indices. The caller supplies
    native table and native-to-export mapping; polygon membership is never inferred.
    """
    explicit={} if explicit is None else explicit
    selected=[s if isinstance(s,Section) else Section(**s) for s in sections]
    selected=[s for s in selected if s.lod==selected_lod]
    used=set(); slots={}; diagnostics=[]
    for section in selected:
        index=section.material_index
        if index<0 or index>=len(material_table): raise IndexError('Invalid native material-table index '+str(index))
        if section.slot_index<0: raise IndexError('Invalid export slot')
        used.add(index)
        identity=explicit.get(section.section_index,material_table[index])
        if section.slot_index in slots and slots[section.slot_index]!=identity: raise ValueError('Conflicting native sections share export slot '+str(section.slot_index))
        slots[section.slot_index]=identity
    for index in range(len(material_table)):
        if index not in used: diagnostics.append('unused_native_material_index:'+str(index))
    unknown=set(explicit)-{s.section_index for s in selected}
    if unknown: raise IndexError('Explicit binding refers to unavailable selected-LOD sections: '+repr(sorted(unknown)))
    return BindingResult(tuple(sorted(slots.items())),tuple(diagnostics))


def _snapshot(slot,object_override):
    original_link=getattr(slot,'link',None)
    original_material=slot.material
    hidden_object_material=None; captured_hidden=False
    if object_override and original_link=='DATA':
        try:
            slot.link='OBJECT'
            hidden_object_material=slot.material; captured_hidden=True
        finally: slot.link=original_link
    return (slot,original_link,original_material,captured_hidden,hidden_object_material)


def _restore(snapshot):
    slot,link,material,captured_hidden,hidden=snapshot
    if captured_hidden:
        slot.link='OBJECT'; slot.material=hidden; slot.link=link
    else:
        if link is not None: slot.link=link
        slot.material=material


def assign_many_transaction(targets,material_factory,identity_key=None, *, object_override=True):
    """Prepare shared graphs once, then atomically bind every specified owner.

    targets contains (object, BindingResult or (slot, identity) iterable). No polygon
    index, geometry, slot count or unselected slot changes. OBJECT links are the safe
    default for shared mesh owners; unrelated DATA-linked owners retain their source
    material. Passing object_override=False explicitly requests existing link behavior.
    On failure both visible DATA/OBJECT links and hidden object overrides are restored.
    """
    if threading.current_thread() is not threading.main_thread(): raise RuntimeError('Material assignment requires the main thread')
    cache={}; requests=[]; seen={}; shared_data={}
    for obj,bindings in targets:
        assignments=bindings.assignments if isinstance(bindings,BindingResult) else tuple(bindings)
        for index,identity in assignments:
            if index<0 or index>=len(obj.material_slots): raise IndexError('Export slot unavailable: '+str(index))
            key=identity_key(identity) if identity_key else identity
            destination=(id(obj),index)
            if destination in seen:
                if seen[destination]!=key: raise ValueError('Conflicting requests for the same owner slot')
                continue
            seen[destination]=key
            if not object_override and getattr(obj.material_slots[index],'link',None)=='DATA':
                data_destination=(id(getattr(obj,'data',obj)),index)
                if data_destination in shared_data and shared_data[data_destination]!=key: raise ValueError('Conflicting shared DATA material assignments')
                shared_data[data_destination]=key
            requests.append((obj,index,identity,key))
    # No user slot is touched while external graph factories run.
    prepared=[]
    for obj,index,identity,key in requests:
        if key not in cache: cache[key]=material_factory(identity)
        prepared.append((obj,index,cache[key]))
    snapshots=[_snapshot(obj.material_slots[index],object_override) for obj,index,_ in prepared]
    try:
        for obj,index,material in prepared:
            slot=obj.material_slots[index]
            if object_override and getattr(slot,'link',None) is not None: slot.link='OBJECT'
            slot.material=material
    except Exception as error:
        failures=[]
        for snapshot in reversed(snapshots):
            try: _restore(snapshot)
            except Exception as rollback_error: failures.append(str(rollback_error))
        if failures: raise RuntimeError('Material assignment and rollback failed: '+repr(failures)) from error
        raise
    return tuple(prepared)


def assign_transaction(obj,bindings,material_factory,identity_key=None, *, object_override=True):
    return tuple((index,material) for _,index,material in assign_many_transaction(((obj,bindings),),material_factory,identity_key,object_override=object_override))


def assign_slot_transaction(obj,slot_index,identity,material_factory,identity_key=None, *, object_override=True):
    """An explicit per-object single-slot override; no first-match inference."""
    return assign_transaction(obj,((slot_index,identity),),material_factory,identity_key,object_override=object_override)
