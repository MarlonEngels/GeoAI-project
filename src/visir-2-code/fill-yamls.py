import os
import yaml
import shutil

NAMELIST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "VISIR-2_v6", "__namelist"
) + os.sep
SUBFOLDERS = {
    "campi":  ("_b_Campi", "fields"),
    "tracce": ("_d_Tracce", "route"),
}

# Custom YAML dumper: writes None as empty value instead of "null".
# VISIR-2 reads YAMLs with yaml.BaseLoader (all scalars → strings),
# so "null" would become the string "null" rather than Python None.
class _NullSafeDumper(yaml.SafeDumper):
    pass

def _represent_none(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:null', '')

_NullSafeDumper.add_representer(type(None), _represent_none)


def create_new_yaml_files():
    """Delete old tyrr_fields/tyrr_route YAMLs and copy fresh ones from templates.

    Returns 'tyrr' (the fixed stem used for all runs).
    """
    name_temp = "tyrr"
    for folder, suffix in SUBFOLDERS.values():
        target = os.path.join(NAMELIST_PATH, folder, f"{name_temp}_{suffix}.yaml")
        template = os.path.join(NAMELIST_PATH, folder, f"template_{folder.split('_')[-1].capitalize()}.yaml")
        # Delete old file if it exists
        try:
            os.remove(target)
        except OSError:
            pass
        shutil.copy(template, target)
    return name_temp


def deep_update(base, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value


def fill_yaml(name_temp, updates, category):
    folder, suffix = SUBFOLDERS[category]
    filepath = f"{NAMELIST_PATH}{folder}/{name_temp}_{suffix}.yaml"
    with open(filepath, 'r') as f:
        data = yaml.safe_load(f)
    deep_update(data, updates)
    with open(filepath, 'w') as f:
        yaml.dump(data, f, Dumper=_NullSafeDumper, default_flow_style=False, sort_keys=False)
