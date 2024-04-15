# -*- coding: UTF-8 -*-
from tinyscript import ensure_str

from ..parsers import *


__all__ = [
    # utils
   "get_pe_data", "valid_names",
    # modifiers
   "add_API_to_IAT", "add_lib_to_IAT", "add_section", "append_to_section", "move_entrypoint_to_new_section",
   "move_entrypoint_to_slack_space", "set_checksum",
]


# --------------------------------------------------- Utils ------------------------------------------------------------
def get_pe_data():
    """ Derive other PE-specific data from this of ~/.packing-box/data/pe. """
    from ....helpers.data import get_data
    d = {k: v for k, v in get_data("PE").items() if k in ["COMMON_API_IMPORTS", "COMMON_DLL_IMPORTS",
                                                          "COMMON_PACKER_SECTION_NAMES", "STANDARD_SECTION_NAMES"]}
    dll = d.pop('COMMON_DLL_IMPORTS')
    d['COMMON_API_IMPORTS'] = [(lib, api) for lib in dll for api in dll[lib]]
    for k in ["COMMON_PACKER_SECTION_NAMES", "STANDARD_SECTION_NAMES"]:
        d[k] = valid_names(d[k])
    return d


valid_names = lambda nl: list(filter(lambda n: len(n) <= 8, map(lambda x: x if isinstance(x, str) else \
                                                                getattr(x, "real_name", getattr(x, "name", "")), nl)))


# ------------------------------------------------- Modifiers ----------------------------------------------------------
def add_API_to_IAT(*args):
    """ Add a function to the IAT. If no function from this library is imported in the binary yet, the library is added
         to the binary.
    
    :param args: either (library, api) or ((library, api), )
    """
    if len(args) == 1:
        lib_name, api = args[0]
    elif len(args) == 2:
        lib_name, api = args
    else:
        raise ValueError("Library and API names shall be provided")
    @supported_parsers("lief")
    def _add_API_to_IAT(parsed, logger):
        logger.debug(f">> selected API import: {lib_name} - {api}")
        # Some packers create the IAT at runtime. It is sometimes in an empty section, which has offset 0. In this case,
        #  the header is overwritten by the patching operation. So, in this case, we don't patch at all.
        patch_imports = not parsed.iat.has_section or parsed.iat.section.offset != 0
        for library in parsed.imports:
            if library.name.lower() == lib_name.lower():
                logger.debug(">> adding API import...")
                library.add_entry(api)
                parsed._build_config.update(imports=True, patch_imports=patch_imports)
                return
        add_lib_to_IAT(lib_name)(parsed, logger)
        parsed.get_import(lib_name).add_entry(api)
        parsed._build_config.update(imports=True, patch_imports=patch_imports)
    return _add_API_to_IAT


def add_lib_to_IAT(library):
    """ Add a library to the IAT. """
    @supported_parsers("lief")
    def _add_lib_to_IAT(parsed, logger):
        logger.debug(">> adding library...")
        parsed.add_library(library)
        parsed._build_config['imports'] = True
    return _add_lib_to_IAT


def add_section(name, section_type=None, characteristics=None, data=b""):
    """ Add a section (uses lief.PE.Binary.add_section).
    
    :param name:            name of the new section
    :param section_type:    type of the new section (lief.PE.SECTION_TYPE)
    :param characteristics: characteristics of the new section
    :param data:            content of the new section
    :return:                modifier function
    """
    if len(name) > 8:
        raise ValueError("Section name can't be longer than 8 characters")
    @supported_parsers("lief")
    def _add_section(parsed, logger):
        # sec = lief.PE.Section(name=name, content=list(data), characteristics=characteristics)
        # for some reason, the above API raises a warning in LIEF:
        #  **[section name] content size is bigger than section's header size**
        # source: https://github.com/lief-project/LIEF/blob/master/src/PE/Builder.cpp
        from ..parsers.lief.__common__ import lief
        s = lief.PE.Section(name=name)
        s.content = list(data)
        s.characteristics = characteristics or parsed.SECTION_CHARACTERISTICS['MEM_READ'] | \
                                               parsed.SECTION_CHARACTERISTICS['MEM_EXECUTE']
        parsed.add_section(s, section_type or parsed.SECTION_TYPES['UNKNOWN'])
        parsed._build_config['overlay'] = True
    return _add_section


def append_to_section(name, data):
    """ Append bytes (either raw data or a function run with the target section's available size in the slack space as
         its single parameter) at the end of a section, in the slack space before the next section.
    """
    @supported_parsers("lief")
    def _append_to_section(parsed, logger):
        section = parsed.section(name, True)
        l, fa = len(section.content), parsed.optional_header.file_alignment
        available_size = max(0, section.virtual_size - l)
        d = list(data(available_size)) if callable(data) else list(data)
        logger.debug(f"section: {section.name} - content: {l} - raw size: {section.size} - virtual size: "
                     f"{section.virtual_size} - file alignment: {fa}")
        if available_size == 0:
            logger.debug(f">> {section.name}: no available space")
        elif len(d) > available_size:
            logger.debug(f">> {section.name}: truncating data ({len(d)} bytes) to {available_size} bytes")
            d = d[:available_size]
        # when section's data and raw size are zero, LIEF fails to append data to the target section, hence in this case
        #  we completely recreate the sections
        if l == section.size == 0:
            # save sections data
            sections, sections_data, l = sorted(list(parsed.sections), key=lambda s: s.virtual_address), [], len(d)
            for s in sections:
                sections_data.append({'name': ensure_str(s.name),
                                      'virtual_address': s.virtual_address,
                                      'char': s.characteristics,
                                      'content': list(d) if s == section else list(s.content),
                                      'size': l + [(fa - l % fa), 0][l % fa == 0] if s == section else s.size,
                                      'virtual_size': s.virtual_size,
                                      'modified': s == section})
            # remove all sections
            for s in sections:
                parsed.remove(s)
            # then recreate them with the updated section
            for sd in sections_data:
                new_sec = parsed.add_section(type(section)(content=sd['content'] + [0] * (-len(sd['content']) % fa),
                                                           name=sd['name'], characteristics=sd['char']))
                for k, v in sd.items():
                    if k in ["name", "char", "modified"]:
                        if k == "modified" and v:
                            section = new_sec
                        continue
                    setattr(new_sec, k, v)
            logger.debug(f"section: {new_sec.name} - content: {len(d)} - new raw size: {new_sec.size}")
        # classical case where section's data or raw size is not zero ; normally append data to the end of the section
        else:
            section.content = list(section.content) + d
            l = len(section.content)
            section.size = l + [(fa - l % fa), 0][l % fa == 0]
            logger.debug(f"section: {section.name} - content: {l} - new raw size: {section.size}")
    return _append_to_section


def move_entrypoint_to_new_section(name, section_type=None, characteristics=None, pre_data=b"", post_data=b"", pre_trampoline_bytes=b""):
    """ Set the entrypoint (EP) to a new section added to the binary that contains code to jump back to the original EP.
        The new section contains *pre_data*, then the code to jump back to the original EP, and finally *post_data*.
    """
    @supported_parsers("lief")
    def _move_entrypoint_to_new_section(parsed, logger):
        
        # Get the original entry point
        original_entrypoint = parsed.optional_header.addressof_entrypoint #+ parsed.optional_header.imagebase
        
        # Check if the architecture is 64 bits
        #is_64bits = parsed.optional_header.magic == parsed.PE.MAGIC_PE32_PLUS # lief.PE.PE_TYPE.PE32_PLUS
        is_64bits = parsed.path.format[-2:] == "64"


        # ============ Trampoline ================
        # ASLR aware trampoline code
        if is_64bits:
            # 64-bit
            # ========== Full code =============
            # mov rbx, gs:[0x60h]               # Get the PEB (ProcessEnvironmentBlock)
            # mov rdi, [rbx+0x10h]              # Get image base address at offset 0x10
            # mov rbx, original_entrypoint
            # add rbx, rdi
            # jmp rbx
            # =================================

            entrypoint_data = [
                0x65, 0x48, 0x8B, 0x1C, 0x25, 0x60, 0x00, 0x00, 0x00,  # mov rbx, gs:[0x60h]
                0x48, 0x8B, 0x7B, 0x10,  # mov rdi, [rbx+0x10h]
                # Here, RDI contains the ImageBaseAddress
                *pre_trampoline_bytes,  # Insert pre_trampoline_bytes here (it should use RDI as the base address)

                0x48, 0xBB,  # mov rbx, original_entrypoint
                0x48, 0x01, 0xFB,  # add rbx, rdi
                
                0x53, 0xC3  # push rbx; ret
                #0xFF, 0xE3  # ==OR== jmp rbx
            ]
            
            # === OLD ===
            #entrypoint_data = [0x48, 0xb8] + list(original_entrypoint.to_bytes(8, "little")) # mov rax, original_entrypoint
            #entrypoint_data += [0x50] # push rax
            #entrypoint_data += [0xc3] # ret
            # === === ===

        else:
            # 32-bit
            
            # ==== Full code ====
            # mov ebx, fs:[0x30]
            # mov eax, [ebx+8]
            # add eax, original_entrypoint
            # push eax; ret
            # ===================

            entrypoint_data = [
                0x64, 0x8B, 0x1D, 0x30, 0x00, 0x00, 0x00,   # mov ebx, fs:[0x30]
                0x8B, 0x7B, 0x08,                           # mov edi, [ebx+8]
                # Here, EDI contains the ImageBaseAddress
                *pre_trampoline_bytes,# Insert pre_trampoline_bytes here (it should use EDI as the base address)

                0xB8, *list(original_entrypoint.to_bytes(4, "little")), # mov eax, original_entrypoint
                0x01, 0xF8,                                 # add eax, edi
                0x50, 0xC3                                  # push eax; ret
                #0xFF, 0xE0                                 # OR jmp eax
            ]

            # === OLD ===
            #new_section_entry.content = [0x68] + list(original_entrypoint.to_bytes(4, "little"))  + [0xc3] # PUSH original_entrypoint; RET
            # === === ===
    
        
        add_section(name, section_type or parsed.SECTION_TYPES['TEXT'], characteristics,
                    list(pre_data) + entrypoint_data + list(post_data))(parsed, logger)
        parsed.optional_header.addressof_entrypoint = parsed.get_section(name).virtual_address + len(pre_data)
    return _move_entrypoint_to_new_section


def move_entrypoint_to_slack_space(section_input, pre_data=b"", post_data_source=b""):
    """ Set the entrypoint (EP) to a new section added to the binary that contains code to jump back to the original EP.
    """
    @supported_parsers("lief")
    def _move_entrypoint_to_slack_space(parsed, logger):
        if parsed.optional_header.section_alignment % parsed.optional_header.file_alignment != 0:
            raise ValueError("SectionAlignment is not a multiple of FileAlignment (file integrity cannot be assured)")
        address_bitsize = [64, 32]["32" in parsed.path.format]
        original_entrypoint = parsed.optional_header.addressof_entrypoint + parsed.optional_header.imagebase
        #  push current_entrypoint
        #  ret
        entrypoint_data = [0x68] + list(original_entrypoint.to_bytes([4, 8][parsed.path.format[-2:] == "64"], 'little')) + [0xc3]
        # other possibility:
        #  mov eax current_entrypoint
        #  jmp eax
        #  entrypoint_data = [0xb8] + list(original_entrypoint.to_bytes(4, 'little')) + [0xff, 0xe0]
        d = list(pre_data) + entrypoint_data
        if callable(post_data_source):
            full_data = lambda l: d + list(post_data_source(l - len(d)))
            add_size = section.size - len(section.content)
        else:
            full_data = d + list(post_data_source)
            add_size = len(full_data)
        s = parsed.section(name, True)
        new_entry = section.virtual_address + len(section.content) + len(pre_data)
        append_to_section(section, full_data)(parsed, logger)
        s.virtual_size += add_size
        parsed.optional_header.addressof_entrypoint = new_entry
    return _move_entrypoint_to_slack_space


def set_checksum(value):
    """ Set the checksum. """
    @supported_parsers("lief")
    def _set_checksum(parsed, logger):
        parsed.optional_header.checksum = value
    return _set_checksum

