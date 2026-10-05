// https://codeforces.com/blog/entry/67391
//
// Optimized revision:
//  * the whole input is read once (one big read instead of per-line plumbing)
//  * tokens are borrowed slices: scalar() allocates nothing, no String per token
//  * manual integer parsers (u64/usize/i32/i64) avoid str::parse overhead
//  * parse failure panics with the offending token instead of Option::unwrap()
//  * hitting EOF panics instead of looping forever
//  * line helpers (raw/text) work on the same cursor: use them either before
//    token-reading or on their own, not interleaved mid-line
//
// Not suitable for interactive problems (the input is consumed up front).
use std::io::Read;
use std::str::FromStr;

pub struct Input {
    data: String,
    pos: usize,
}

impl Input {
    pub fn new<R: Read>(mut std: R) -> Self {
        let mut data = String::new();
        std.read_to_string(&mut data).unwrap();
        return Self { data, pos: 0 };
    }

    #[inline]
    fn skip_ws(&mut self) {
        let bytes = self.data.as_bytes();
        while self.pos < bytes.len() && bytes[self.pos].is_ascii_whitespace() {
            self.pos += 1;
        }
    }

    /// Move to the start of the next line (no-op when already there), so that
    /// line helpers behave like the token reader's "whole line was consumed".
    #[inline]
    fn line_start(&mut self) {
        let bytes = self.data.as_bytes();
        if self.pos != 0 && bytes.get(self.pos - 1) != Some(&b'\n') {
            while self.pos < bytes.len() && bytes[self.pos] != b'\n' {
                self.pos += 1;
            }
            if self.pos < bytes.len() {
                self.pos += 1;
            }
        }
    }

    /// Zero-copy next token.
    pub fn token(&mut self) -> &str {
        self.skip_ws();
        let bytes = self.data.as_bytes();
        let start = self.pos;
        while self.pos < bytes.len() && !bytes[self.pos].is_ascii_whitespace() {
            self.pos += 1;
        }
        return &self.data[start..self.pos];
    }

    /// Next line, including the trailing '\n' when present.
    pub fn raw(&mut self) -> String {
        self.line_start();
        let bytes = self.data.as_bytes();
        let start = self.pos;
        while self.pos < bytes.len() && bytes[self.pos] != b'\n' {
            self.pos += 1;
        }
        if self.pos < bytes.len() {
            self.pos += 1;
        }
        return self.data[start..self.pos].to_string();
    }

    /// Next line, trimmed.
    pub fn text(&mut self) -> String {
        self.line_start();
        let bytes = self.data.as_bytes();
        let start = self.pos;
        while self.pos < bytes.len() && bytes[self.pos] != b'\n' {
            self.pos += 1;
        }
        let end = self.pos;
        if self.pos < bytes.len() {
            self.pos += 1;
        }
        return self.data[start..end].trim().to_string();
    }

    pub fn next(&mut self) -> String {
        return self.token().to_string();
    }

    pub fn scalar<T: FromStr>(&mut self) -> T {
        let token = self.token();
        return match token.parse() {
            Ok(value) => value,
            Err(_) => panic!("Input::scalar: cannot parse {:?}", token),
        };
    }

    pub fn vector<T: FromStr>(&mut self, n: usize) -> Vec<T> {
        return (0..n).map(|_| self.scalar()).collect();
    }

    /// Fast unsigned parser (also the base for usize/i32/i64).
    #[inline]
    pub fn u64(&mut self) -> u64 {
        self.skip_ws();
        let bytes = self.data.as_bytes();
        let mut value: u64 = 0;
        while self.pos < bytes.len() {
            let digit = bytes[self.pos].wrapping_sub(b'0');
            if digit > 9 {
                break;
            }
            value = value.wrapping_mul(10).wrapping_add(digit as u64);
            self.pos += 1;
        }
        if self.pos < bytes.len() && bytes[self.pos].is_ascii_whitespace() {
            self.pos += 1;
        }
        return value;
    }

    #[inline]
    pub fn usize(&mut self) -> usize {
        return self.u64() as usize;
    }

    #[inline]
    pub fn i64(&mut self) -> i64 {
        self.skip_ws();
        if self.pos < self.data.len() && self.data.as_bytes()[self.pos] == b'-' {
            self.pos += 1;
            return (self.u64() as i64).wrapping_neg();
        }
        return self.u64() as i64;
    }

    #[inline]
    pub fn i32(&mut self) -> i32 {
        return self.i64() as i32;
    }

    /// True when only whitespace is left.
    pub fn is_eof(&self) -> bool {
        let bytes = self.data.as_bytes();
        let mut i = self.pos;
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        return i >= bytes.len();
    }
}
