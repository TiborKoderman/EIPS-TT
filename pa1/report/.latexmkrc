# LaTeXmk configuration for outputting auxiliary files to .out directory

# Set output directory for auxiliary files
$out_dir = '.out';

# PDF generation mode (use pdflatex)
$pdf_mode = 1;

# Ensure the output directory exists
system("mkdir -p $out_dir");
