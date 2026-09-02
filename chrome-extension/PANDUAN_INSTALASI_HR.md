# Panduan Instalasi Greenhouse Resume Exporter

Panduan ini ditujukan untuk tim HR. Anda tidak perlu menggunakan Terminal atau
memahami pemrograman.

## Fungsi ekstensi

Greenhouse Resume Exporter membantu Anda:

- melihat daftar lowongan yang dapat Anda akses;
- memilih satu lowongan;
- menghitung jumlah kandidat;
- meminta Greenhouse mengirimkan resume melalui email.

Greenhouse membatasi satu email maksimal untuk 30 kandidat. Sebagai contoh,
300 kandidat akan diproses menjadi sekitar 10 email. Kandidat yang tidak
memiliki resume dapat dilewati oleh Greenhouse.

## Sebelum memulai

Pastikan Anda memiliki:

- komputer kantor dengan Google Chrome;
- akun Greenhouse yang aktif;
- izin untuk melihat kandidat dan mengunduh resume;
- folder ekstensi `chrome-extension` dari administrator atau tim IT.

## Cara memasang ekstensi

### 1. Simpan folder ekstensi

Jika Anda menerima file ZIP dari tim IT:

1. Buka folder **Downloads**.
2. Klik dua kali file ZIP untuk mengekstraknya.
3. Pastikan terdapat folder bernama `chrome-extension`.
4. Jangan menghapus atau memindahkan folder tersebut setelah ekstensi dipasang.

### 2. Buka halaman ekstensi Chrome

1. Buka Google Chrome.
2. Ketik alamat berikut pada kolom alamat:

   `chrome://extensions`

3. Tekan **Enter**.

### 3. Aktifkan mode pengembang

1. Cari tombol **Developer mode** atau **Mode developer** di kanan atas.
2. Aktifkan tombol tersebut.

Mode ini hanya diperlukan untuk memasang versi internal yang diberikan oleh
tim IT.

### 4. Muat ekstensi

1. Klik **Load unpacked** atau **Muat yang belum dipaketkan**.
2. Pilih folder `chrome-extension` yang sudah diekstrak.
3. Klik **Select** atau **Pilih**.
4. Pastikan **Greenhouse Resume Exporter** muncul dan dalam keadaan aktif.

### 5. Sematkan ekstensi

1. Klik ikon berbentuk puzzle di kanan atas Chrome.
2. Cari **Greenhouse Resume Exporter**.
3. Klik ikon pin agar ekstensi selalu terlihat di toolbar.

## Cara menggunakan

### 1. Masuk ke Greenhouse

1. Buka `https://app.greenhouse.io/alljobs`.
2. Login menggunakan akun kerja Anda.
3. Selesaikan verifikasi tambahan jika diminta.
4. Tunggu sampai daftar lowongan terlihat.

### 2. Pilih lowongan

1. Pastikan tab Greenhouse masih aktif.
2. Klik ikon **Greenhouse Resume Exporter** di toolbar Chrome.
3. Pilih lowongan dari daftar **Vacancy**.
4. Klik **Count candidates**.

Ekstensi akan menampilkan:

- jumlah kandidat;
- perkiraan jumlah email;
- ukuran setiap kelompok, yaitu maksimal 30 kandidat.

### 3. Mulai ekspor

1. Periksa kembali nama lowongan dan jumlah kandidat.
2. Klik **Export resumes**.
3. Baca informasi konfirmasi.
4. Klik **Submit export** jika semua informasi sudah benar.
5. Tunggu sampai muncul pesan bahwa semua kelompok berhasil dikirim.

Greenhouse akan memproses resume dan mengirimkan PDF ke email yang terhubung
dengan akun Greenhouse Anda. Proses email dapat membutuhkan beberapa menit.

> Penting: Jangan menekan **Submit export** berulang kali. Setiap pengiriman
> ulang dapat menghasilkan email duplikat.

## Jika ekstensi tidak bekerja

### Daftar lowongan tidak muncul

1. Buka `https://app.greenhouse.io/alljobs`.
2. Pastikan Anda sudah login dan daftar lowongan terlihat.
3. Tetap berada di tab Greenhouse tersebut.
4. Buka ekstensi dan klik **Retry connection**.

### Lowongan tertentu tidak tersedia

Ekstensi hanya menampilkan lowongan yang dapat diakses oleh akun Greenhouse
Anda. Hubungi administrator Greenhouse atau tim HR Operations jika Anda
memerlukan akses tambahan.

### Tombol ekspor tidak aktif

Pilih lowongan terlebih dahulu, kemudian klik **Count candidates**. Tombol
ekspor baru aktif setelah proses penghitungan selesai.

### Tidak menerima email

1. Tunggu beberapa menit.
2. Periksa folder **Spam** atau **Junk**.
3. Pastikan alamat email akun Greenhouse Anda benar.
4. Hubungi tim IT jika ekstensi menampilkan pesan gagal.

### Setelah ekstensi diperbarui oleh tim IT

1. Buka `chrome://extensions`.
2. Cari **Greenhouse Resume Exporter**.
3. Klik ikon **Reload**.
4. Muat ulang halaman Greenhouse.

## Keamanan data

- Gunakan ekstensi hanya pada komputer dan akun kerja yang disetujui.
- Jangan meneruskan resume kandidat kepada pihak yang tidak berwenang.
- Jangan membagikan folder ekstensi di luar perusahaan.
- Hapus file resume setelah tidak lagi diperlukan sesuai kebijakan perusahaan.
- Ekstensi tidak meminta atau menyimpan kata sandi Greenhouse Anda.

## Meminta bantuan

Saat melaporkan masalah kepada tim IT, sertakan:

- nama lowongan;
- waktu kejadian;
- pesan kesalahan yang terlihat pada ekstensi;
- tangkapan layar tanpa menampilkan data kandidat yang sensitif.

