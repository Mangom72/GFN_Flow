-- Observe clicks without consuming them, reading input devices, or taking focus.
-- Handles live reinstall as well as normal compositor config reloads.
for _, binding in ipairs(_G.macro_studio_click_binds or {}) do
    binding:remove()
end
_G.macro_studio_click_binds = {}

local function outside_click()
    for _, layer in ipairs(hl.get_layers({ namespace = "gfn-macro-picker" })) do
        if layer.mapped then
            local point = hl.get_cursor_pos()
            if not point then return end
            if point.x < layer.x or point.x >= layer.x + layer.w
                or point.y < layer.y or point.y >= layer.y + layer.h then
                local script = os.getenv("HOME") .. "/Projects/auto_macro/auto_macro.py"
                hl.exec_cmd(string.format('/usr/bin/python3 %q pointer --point %.0f %.0f --surface %s',
                    script, point.x, point.y, layer.address))
            end
            return
        end
    end
end

for _, button in ipairs({272, 273, 274}) do
    table.insert(_G.macro_studio_click_binds, hl.bind("mouse:" .. button, outside_click, {
        non_consuming = true,
        ignore_mods = true,
        transparent = true,
        description = "Macro Studio: dismiss outside click",
    }))
end
