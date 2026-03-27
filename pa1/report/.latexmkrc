# LaTeXmk configuration for outputting auxiliary files to .out directory

# Set output directory for auxiliary files
$out_dir = '.out';

# PDF generation mode (use pdflatex)
$pdf_mode = 1;

# Ensure the output directory exists (cross-platform)
if ( !-d $out_dir ) {
    mkdir $out_dir or die "Cannot create output directory '$out_dir': $!";
}
