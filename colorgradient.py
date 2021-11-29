import math
from colour import Color

def create_color(one, two, num_steps):
    r1 = one.red * 255
    g1 = one.green * 255
    b1 = one.blue * 255

    r2 = two.red * 255
    g2 = two.green * 255
    b2 = two.blue * 255

    result = []
    for i in range(0, num_steps):
        i_norm = i * 1.0 / (num_steps - 1)
        r_n = math.floor(r1 + i_norm * (r2 - r1))
        g_n = math.floor(g1 + i_norm * (g2 - g1))
        b_n = math.floor(b1 + i_norm * (b2 - b1))
        result.append(Color(rgb=(r_n / 255, g_n / 255, b_n / 255)))

    return result

def create_multi_color(colors, num_steps):
    num_sections = len(colors) - 1

    result = []

    for section in range(0, num_sections):
        sub_section = create_color(colors[section], colors[section + 1], num_steps // num_sections)

        for c in sub_section:
            result.append(c)

    if len(result) < num_steps:
        for c in range(len(result), num_steps):
            result.append(colors[len(colors) - 1])

    return result
