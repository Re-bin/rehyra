#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::int64_t kMod = 998244353;
constexpr std::int64_t kPrimitiveRoot = 3;
constexpr std::size_t kMaximumTransformSize = 1U << 23;

std::int64_t modular_power(std::int64_t base, std::int64_t exponent) {
    std::int64_t result = 1;
    while (exponent > 0) {
        if (exponent & 1) {
            result = result * base % kMod;
        }
        base = base * base % kMod;
        exponent >>= 1;
    }
    return result;
}

void ntt(std::vector<std::int64_t>& values, bool invert) {
    const std::size_t size = values.size();
    for (std::size_t i = 1, j = 0; i < size; ++i) {
        std::size_t bit = size >> 1;
        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            std::swap(values[i], values[j]);
        }
    }

    for (std::size_t length = 2; length <= size; length <<= 1) {
        std::int64_t root =
            modular_power(kPrimitiveRoot, (kMod - 1) / static_cast<std::int64_t>(length));
        if (invert) {
            root = modular_power(root, kMod - 2);
        }
        for (std::size_t start = 0; start < size; start += length) {
            std::int64_t factor = 1;
            for (std::size_t offset = 0; offset < length / 2; ++offset) {
                const std::int64_t left = values[start + offset];
                const std::int64_t right =
                    values[start + offset + length / 2] * factor % kMod;
                values[start + offset] = left + right;
                if (values[start + offset] >= kMod) {
                    values[start + offset] -= kMod;
                }
                values[start + offset + length / 2] = left - right;
                if (values[start + offset + length / 2] < 0) {
                    values[start + offset + length / 2] += kMod;
                }
                factor = factor * root % kMod;
            }
        }
    }

    if (invert) {
        const std::int64_t inverse_size =
            modular_power(static_cast<std::int64_t>(size), kMod - 2);
        for (std::int64_t& value : values) {
            value = value * inverse_size % kMod;
        }
    }
}

std::vector<std::int64_t> read_integer_array(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open input");
    }
    const std::string text(
        (std::istreambuf_iterator<char>(input)),
        std::istreambuf_iterator<char>());

    std::size_t position = 0;
    while (position < text.size() && std::isspace(static_cast<unsigned char>(text[position]))) {
        ++position;
    }
    bool wrapped = false;
    if (position < text.size() && text[position] == '{') {
        wrapped = true;
        ++position;
        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
        if (text.compare(position, 3, "\"A\"") != 0) {
            throw std::runtime_error("the only supported object wrapper is {\"A\": [...]}");
        }
        position += 3;
        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
        if (position == text.size() || text[position] != ':') {
            throw std::runtime_error("malformed A wrapper");
        }
        ++position;
        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
    }
    if (position == text.size() || text[position] != '[') {
        throw std::runtime_error("input must be a JSON integer array or {\"A\": [...]}");
    }
    ++position;

    std::vector<std::int64_t> values;
    while (true) {
        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
        if (position < text.size() && text[position] == ']') {
            ++position;
            break;
        }

        bool negative = false;
        if (position < text.size() && text[position] == '-') {
            negative = true;
            ++position;
        }
        if (position == text.size() ||
            !std::isdigit(static_cast<unsigned char>(text[position]))) {
            throw std::runtime_error("input contains a non-integer value");
        }

        std::int64_t value = 0;
        while (position < text.size() &&
               std::isdigit(static_cast<unsigned char>(text[position]))) {
            const int digit = text[position] - '0';
            if (value > (std::numeric_limits<std::int64_t>::max() - digit) / 10) {
                throw std::runtime_error("integer overflow");
            }
            value = value * 10 + digit;
            ++position;
        }
        values.push_back(negative ? -value : value);

        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
        if (position == text.size() || text[position] != ',') {
            if (position < text.size() && text[position] == ']') {
                continue;
            }
            throw std::runtime_error("malformed JSON integer array");
        }
        ++position;
    }

    while (position < text.size() && std::isspace(static_cast<unsigned char>(text[position]))) {
        ++position;
    }
    if (wrapped) {
        if (position == text.size() || text[position] != '}') {
            throw std::runtime_error("malformed A wrapper");
        }
        ++position;
        while (position < text.size() &&
               std::isspace(static_cast<unsigned char>(text[position]))) {
            ++position;
        }
    }
    if (position != text.size() || values.size() < 2) {
        throw std::runtime_error("input must contain at least two integers");
    }
    return values;
}

std::size_t support_size(
    const std::vector<std::int64_t>& left,
    const std::vector<std::int64_t>& right,
    std::size_t output_size) {
    std::vector<std::int64_t> left_transform = left;
    std::vector<std::int64_t> right_transform = right;
    ntt(left_transform, false);
    ntt(right_transform, false);
    for (std::size_t i = 0; i < left_transform.size(); ++i) {
        left_transform[i] = left_transform[i] * right_transform[i] % kMod;
    }
    ntt(left_transform, true);
    return static_cast<std::size_t>(std::count_if(
        left_transform.begin(),
        left_transform.begin() + static_cast<std::ptrdiff_t>(output_size),
        [](std::int64_t value) { return value != 0; }));
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 2) {
            throw std::runtime_error("usage: verify_sums_exact CAPTURED_STATE.json");
        }
        std::vector<std::int64_t> values = read_integer_array(argv[1]);
        std::sort(values.begin(), values.end());
        if (std::adjacent_find(values.begin(), values.end()) != values.end()) {
            throw std::runtime_error("input contains duplicate integers");
        }
        if (values.size() >= static_cast<std::size_t>(kMod)) {
            throw std::runtime_error("set is too large for exact support recovery");
        }

        const std::int64_t minimum = values.front();
        const std::int64_t maximum = values.back();
        const std::size_t span = static_cast<std::size_t>(maximum - minimum);
        const std::size_t output_size = 2 * span + 1;
        std::size_t transform_size = 1;
        while (transform_size < output_size) {
            transform_size <<= 1;
        }
        if (transform_size > kMaximumTransformSize) {
            throw std::runtime_error("integer span exceeds the exact NTT capacity");
        }

        std::vector<std::int64_t> indicator(transform_size);
        std::vector<std::int64_t> reversed(transform_size);
        for (std::int64_t value : values) {
            const std::size_t index = static_cast<std::size_t>(value - minimum);
            indicator[index] = 1;
            reversed[span - index] = 1;
        }

        const std::size_t sumset_size = support_size(indicator, indicator, output_size);
        const std::size_t diffset_size = support_size(indicator, reversed, output_size);
        const double set_size = static_cast<double>(values.size());
        const double score =
            std::log(static_cast<double>(sumset_size) / set_size) /
            std::log(static_cast<double>(diffset_size) / set_size);

        std::cout << std::setprecision(17)
                  << "{\"qualified\":true"
                  << ",\"arithmetic\":\"exact-ntt-mod-998244353\""
                  << ",\"set_size\":" << values.size()
                  << ",\"minimum\":" << minimum
                  << ",\"maximum\":" << maximum
                  << ",\"span\":" << span
                  << ",\"sumset_size\":" << sumset_size
                  << ",\"diffset_size\":" << diffset_size
                  << ",\"c_a\":" << score
                  << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
